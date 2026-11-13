"""Save-target and return-result checks with fake mode stores, no instrument."""
import contextlib
import io
from pathlib import Path
import runpy
import types
import unittest
import test_calibration_offline  # Installs isolated .NET module stubs.

with contextlib.redirect_stdout(io.StringIO()):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "SaveBeamModeCheck.py"))
Check = module["SaveBeamModeCheck"]


class ModeStore:
    def __init__(self):
        self.mode = "test beam mode"
        self.state = "Changed"
        self.valid = False
        self.calls = []
        self.result = True
        self.on_save = None

    def GetActualSettingsName(self):
        return self.mode

    def GetSettingsState(self):
        return self.state

    def ToolCalibrationDataIsValid(self):
        return self.valid

    def BeamCurrentGetLastMeasureInA(self):
        return 4.1971975e-11

    def SaveSettings(self, *args):
        if args:
            raise AssertionError("No dataset ID may be guessed from the display name")
        self.calls.append("SaveSettings()")
        if self.on_save:
            self.on_save()
        return self.result


class SaveTargetChecks(unittest.TestCase):
    def setUp(self):
        self.tm = ModeStore()
        self.cc = ModeStore()
        self.logs = []
        self.check = Check(self.tm, types.SimpleNamespace(Output=self.logs.append), self.cc)

    def test_inspection_reads_target_without_saving_or_setting_validity(self):
        self.check.inspect()
        self.assertEqual(self.tm.calls, [])
        self.assertFalse(self.tm.valid)
        self.assertEqual(self.check.expected, ("test beam mode", "test beam mode"))
        self.assertFalse(self.check.saved)

    def test_cannot_save_before_target_has_been_read(self):
        self.check.save()
        self.assertEqual(self.tm.calls, [])

    def test_empty_mode_blocks_save(self):
        self.tm.mode = ""
        self.check.inspect()
        self.check.save()
        self.assertIsNone(self.check.expected)
        self.assertEqual(self.tm.calls, [])

    def test_mode_change_before_save_prevents_write(self):
        self.check.inspect()
        self.tm.mode = "different mode"
        self.check.save()
        self.assertEqual(self.tm.calls, [])
        self.assertEqual(self.check.outcome, "failed")

    def test_column_change_before_save_prevents_write(self):
        self.check.inspect()
        self.cc.mode = "different column mode"
        self.check.save()
        self.assertEqual(self.tm.calls, [])

    def test_success_uses_parameterless_call_and_prevents_duplicate_click(self):
        self.check.inspect()
        self.check.save()
        self.check.save()
        self.assertEqual(self.tm.calls, ["SaveSettings()"])
        self.assertTrue(self.check.saved)
        self.assertEqual(self.check.outcome, "reported_saved")
        self.assertFalse(self.tm.valid)  # No manual calibration-valid mutation.
        self.assertTrue(any("4.197197500e-11 A" in line for line in self.logs))

    def test_false_return_is_not_reported_as_saved(self):
        self.tm.result = False
        self.check.inspect()
        self.check.save()
        self.assertFalse(self.check.saved)
        self.assertEqual(self.check.outcome, "failed")

    def test_save_exception_is_not_reported_as_saved(self):
        def fail():
            raise RuntimeError("simulated save failure")
        self.tm.on_save = fail
        self.check.inspect()
        self.check.save()
        self.assertFalse(self.check.saved)
        self.assertFalse(self.check.busy)

    def test_changed_mode_during_save_is_reported_unconfirmed(self):
        self.tm.on_save = lambda: setattr(self.tm, "mode", "unexpected mode")
        self.check.inspect()
        self.check.save()
        self.assertFalse(self.check.saved)
        self.assertEqual(self.check.outcome, "failed")

    def test_explicit_inspection_can_select_a_new_mode(self):
        self.check.inspect()
        self.tm.mode, self.cc.mode = "second beam", "second column"
        self.check.inspect()
        self.check.save()
        self.assertEqual(self.tm.calls, ["SaveSettings()"])
        self.assertTrue(self.check.saved)


if __name__ == "__main__":
    unittest.main()
