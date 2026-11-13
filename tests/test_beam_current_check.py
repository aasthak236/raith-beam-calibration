"""Isolated measurement protocol tests with fake clients; no hardware."""
import contextlib
import io
from pathlib import Path
import runpy
import types
import unittest
import test_calibration_offline as helpers

with contextlib.redirect_stdout(io.StringIO()):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "BeamCurrentMeasurementCheck.py"))
Check = module["BeamCurrentCheck"]


class MeasurementTM:
    def __init__(self):
        self.OnBeamCurrentMeasurement = helpers.Event()
        self.requests = []
        self.accept = True
        self.last_value = 99e-9
        self.getter_calls = 0
        self.during_request = None

    def BeamCurrentStartMeasureAtPos(self, name, drive_back):
        self.requests.append((name, drive_back))
        if self.during_request:
            self.during_request()
        return self.accept

    def BeamCurrentGetLastMeasureInA(self):
        self.getter_calls += 1
        return self.last_value


class MeasurementProtocol(unittest.TestCase):
    def setUp(self):
        self.tm = MeasurementTM()
        self.stage = helpers.Stage()
        self.logs = []
        self.c = Check(self.tm, self.stage, types.SimpleNamespace(Output=self.logs.append))

    def event(self, status, value=2e-9):
        return types.SimpleNamespace(Status=status, BeamCurrentInA=value,
            Position="Faraday Cup on USH", DriveBack=False, Progress=50, Comment="simulation")

    def test_construction_does_not_request_measurement_or_stage_motion(self):
        self.assertEqual(self.tm.requests, [])
        self.assertEqual(self.stage.requests, [])

    def test_start_uses_explicit_cup_name_and_no_drive_back(self):
        self.c.start()
        self.assertEqual(self.tm.requests, [("Faraday Cup on USH", False)])
        self.assertTrue(self.c.running)

    def test_mismatched_cup_prevents_start(self):
        self.stage.names[2] = "another position"
        self.c.start()
        self.assertEqual(self.tm.requests, [])
        self.assertFalse(self.c.running)

    def test_new_value_waits_for_finished_and_is_not_replaced_by_stale_getter(self):
        self.c.start()
        self.c._on_measurement(self.event(helpers.status.NewValue, 2e-9))
        self.assertTrue(self.c.running)
        self.assertFalse(self.c.freshness_verified)
        self.c._on_measurement(self.event(helpers.status.Finished, 0))
        self.assertTrue(self.c.freshness_verified)
        self.assertEqual(self.c.fresh_value_a, 2e-9)
        self.assertEqual(self.c.finished_value_a, 0)
        self.assertEqual(self.c.last_value_a, 99e-9)
        self.assertFalse(self.c.running)
        self.assertEqual(self.tm.OnBeamCurrentMeasurement.handlers, [])

    def test_finished_without_new_value_does_not_validate_old_reading(self):
        self.c.start()
        self.c._on_measurement(self.event(helpers.status.Finished))
        self.assertFalse(self.c.freshness_verified)
        self.assertEqual(self.c.outcome, "unverified")
        self.assertEqual(self.c.last_value_a, 99e-9)

    def test_rejected_request_discards_events_delivered_before_return(self):
        self.tm.accept = False
        self.tm.during_request = lambda: self.c._on_measurement(self.event(helpers.status.Finished))
        self.c.start()
        self.assertEqual(self.c.outcome, "failed")
        self.assertEqual(self.tm.getter_calls, 0)
        self.assertFalse(self.c.running)

    def test_early_events_are_processed_after_start_acceptance(self):
        def early():
            self.c._on_measurement(self.event(helpers.status.NewValue))
            self.c._on_measurement(self.event(helpers.status.Finished))
        self.tm.during_request = early
        self.c.start()
        self.assertTrue(self.c.freshness_verified)
        self.assertEqual(self.c.outcome, "finished")

    def test_failure_detaches_event_and_stops_waiting(self):
        self.c.start()
        self.c._on_measurement(self.event(helpers.status.Failed))
        self.assertFalse(self.c.running)
        self.assertFalse(self.c.timer.active)
        self.assertEqual(self.tm.OnBeamCurrentMeasurement.handlers, [])

    def test_nonfinite_current_is_not_verified(self):
        self.c.start()
        self.c._on_measurement(self.event(helpers.status.NewValue, float("nan")))
        self.c._on_measurement(self.event(helpers.status.Finished))
        self.assertFalse(self.c.freshness_verified)

    def test_zero_reading_is_recorded_without_inventing_acceptance_limit(self):
        self.c.start()
        self.c._on_measurement(self.event(helpers.status.NewValue, 0.0))
        self.c._on_measurement(self.event(helpers.status.Finished))
        self.assertTrue(self.c.freshness_verified)
        self.assertEqual(self.c.fresh_value_a, 0.0)

    def test_subnanoamp_measurement_message_uses_explicit_scientific_notation(self):
        self.c.start()
        self.c._on_measurement(self.event(helpers.status.NewValue, 4.1971975e-11))
        self.c._on_measurement(self.event(helpers.status.Finished, 4.1971975e-11))
        self.assertIn("4.197197500e-11 A (0.041971975 nA)", self.c.message)
        self.assertNotIn(": 0 A", self.c.message)
        self.assertTrue(any("4.197197500e-11" in line and "RESULT:" in line for line in self.logs))

    def test_timeout_does_not_claim_to_cancel_instrument_operation(self):
        self.c.start()
        self.c._on_timeout(None, None)
        self.assertFalse(self.c.running)
        self.assertIn("may still be active", self.c.message)


if __name__ == "__main__":
    unittest.main()
