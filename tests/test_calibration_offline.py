"""Offline event-flow checks. No Raith libraries, GUI, or hardware are used."""
import contextlib
import io
from pathlib import Path
import runpy
import sys
import types
import unittest


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self


class Timer:
    def __init__(self):
        self.Tick = Event()
        self.active = False

    def Start(self):
        self.active = True

    def Stop(self):
        self.active = False

    def Dispose(self):
        self.Stop()


status = types.SimpleNamespace(Started=0, DrivingStage=1, TurnBeam=2, Measure=3,
                              NewValue=4, Finished=5, Failed=6, Unknown=7)
for name in ("System", "System.Drawing", "System.Windows", "System.Windows.Forms", "Raith", "Raith.CommonLib"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["System"].AppDomain = types.SimpleNamespace(
    CurrentDomain=types.SimpleNamespace(GetAssemblies=lambda: []))
sys.modules["System"].Enum = types.SimpleNamespace(Parse=lambda enum_type, member: getattr(enum_type, member))
sys.modules["System.Drawing"].Color = types.SimpleNamespace(**{
    name: name for name in ("DimGray", "ForestGreen", "RoyalBlue", "DarkOrange", "Firebrick", "Black")})
sys.modules["System.Windows.Forms"].Timer = Timer
sys.modules["System.Windows.Forms"].Screen = types.SimpleNamespace(
    PrimaryScreen=types.SimpleNamespace(WorkingArea=types.SimpleNamespace(
        Left=0, Top=0, Width=1024, Height=728)))
sys.modules["Raith.CommonLib"].WorkingDistanceParameterKind = types.SimpleNamespace(
    ActualWorkingDistance=1, WorkingDistanceTarget=2)
sys.modules["Raith.CommonLib"].BeamCurrentMeasurementStatus = status
procedures = types.SimpleNamespace(**{name: name for name in (
    "DynamicFocus", "DynamicStigmator", "DynamicFocusFullField",
    "DynamicStigmatorFullField", "PositionCoarse", "PositionFine", "DistortionUsingStage")})
sys.modules["Raith.CommonLib"].AlignmentProcedure = procedures
sys.modules["Raith.CommonLib"].ComponentStatus = types.SimpleNamespace(Idle="Idle")
with contextlib.redirect_stdout(io.StringIO()):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "AutomaticBeamCalibration.py"),
                           init_globals={"AlignmentProcedure": procedures,
                                         "ComponentStatus": types.SimpleNamespace(Idle="Idle")})
Controller = module["AutomaticBeamCalibration"]
Dashboard = module["CalibrationDashboard"]


def find_step(steps, phase, short_name):
    return next(index for index, step in enumerate(steps)
                if step.get("phase") == phase and step.get("short_name") == short_name)


class Control:
    def __init__(self, text=""):
        self.Text = text
        self.Visible = True
        self.Enabled = True
        self.Click = Event()
        self.Left = self.Top = 0
        self.Width = 100
        self.Height = 20
        self.parent = None

    def ChangeContainer(self, parent):
        self.parent = parent


class DashboardUI:
    def __init__(self):
        self.controls = []
        self.logs = []
        self.image_calls = []
        self.image_links = []
        self.creation_locked = False

    def Output(self, message):
        self.logs.append(message)

    def Label(self, text):
        if self.creation_locked:
            raise AssertionError("Remote GUI control created after window opened")
        control = Control(text)
        self.controls.append(control)
        return control

    GroupBox = Label
    Button = Label

    def ProgressBar(self, width):
        control = self.Label("")
        control.Width = width
        control.Value = 0
        return control

    def ImageBoxIP(self, width, height, live, slot, overview=None):
        if self.creation_locked:
            raise AssertionError("Image control created after window opened")
        self.image_calls.append(slot)
        self.image_links.append(overview)
        control = ImageControl("")
        self.controls.append(control)
        control.Width, control.Height = width, height
        control.SlotOrChannelNumber = slot
        return control


class ImageControl(Control):
    """Model the special image component, which is not a generic remote control."""
    def __init__(self, text=""):
        Control.__init__(self, text)
        self.restricted = True

    def __setattr__(self, name, value):
        if name == "Visible" and getattr(self, "restricted", False):
            raise AssertionError("Generic visibility command sent to special image control")
        object.__setattr__(self, name, value)

    def ChangeContainer(self, parent):
        raise AssertionError("Generic reparent command sent to special image control")


class Stage:
    def __init__(self):
        self.OnSequenceCompleted = Event()
        self.names = ["Faraday Cup on stage", "Faraday Cup on holder",
                      "Faraday Cup on USH", "Nanorods", "Chessy", "sample"]
        self.requests = []
        self.during_request = None

    def GetStoredPositionsDisplayNameList(self):
        return self.names

    def DriveStoredPosition(self, name):
        self.requests.append(name)
        if self.during_request:
            self.during_request()
        return 42


class ToolManager:
    def __init__(self):
        self.OnComponentStatusChanged = Event()
        self.OnBeamCurrentMeasurement = Event()
        self.accept_alignment = True
        self.accept_current = True
        self.starts = 0
        self.alignment_requests = []
        self.result = None
        self.measure_requests = []
        self.mode = "test beam mode"
        self.save_calls = []
        self.accept_save = True
        self.during_save = None

    def GetActualSettingsName(self):
        return self.mode

    def SaveSettings(self):
        self.save_calls.append(self.mode)
        if self.during_save is not None:
            self.during_save()
        return self.accept_save

    def StartAutomaticAlignment(self, procedure, parameters):
        self.starts += 1
        self.alignment_requests.append((procedure, parameters))
        return self.accept_alignment

    def StopAutomaticAlignment(self):
        pass

    def GetLastAlignmentResult(self):
        return self.result

    def BeamCurrentStartMeasure(self):
        return self.accept_current

    def BeamCurrentStartMeasureAtPos(self, name, drive_back):
        self.measure_requests.append((name, drive_back))
        return self.accept_current

    def BeamCurrentGetLastMeasureInA(self):
        return 8e-10


class OfflineEventFlow(unittest.TestCase):
    def setUp(self):
        self.stage = Stage()
        self.tm = ToolManager()
        self.logs = []
        self.completed = []
        ui = types.SimpleNamespace(Output=self.logs.append)
        self.c = Controller(self.tm, ui, {
            "record": lambda step, index: self.completed.append("next") or True
        }, self.stage)
        self.c._alignment_parameters = lambda procedure: "ParabolicAutoFocus"

    def arm(self, step):
        self.c.steps = [step, self.c._custom("next action", "record")]
        self.c.step_index = 0
        self.c.running = True
        self.c.attempt = 1

    def move(self):
        return self.c._custom("move", "move_to_position_list_index",
                              position_role="nanorods", position_list_index=3)

    def sequence_event(self, sequence=42, success=True):
        return types.SimpleNamespace(Id=sequence, Success=success,
                                     ErrorL=-1, ErrorX=1, ErrorY=0, ErrorZ=-1,
                                     ErrorR=-1, ErrorT=-1)

    def current_event(self, state, value=1.2e-9):
        return types.SimpleNamespace(Status=state, BeamCurrentInA=value, Comment="simulation",
                                     Position="Faraday Cup on USH", DriveBack=False)

    def test_unrelated_sequence_does_not_advance(self):
        self.arm(self.move())
        self.c._run_next_step()
        self.assertEqual(self.stage.requests, ["Nanorods"])
        self.c._on_sequence_completed(self.stage, self.sequence_event(99))
        self.assertEqual(self.completed, [])
        self.c._on_sequence_completed(self.stage, self.sequence_event())
        self.assertEqual(self.completed, ["next"])

    def test_failed_sequence_prevents_next_operation(self):
        self.arm(self.move())
        self.c._run_next_step()
        self.c._on_sequence_completed(self.stage, self.sequence_event(success=False))
        self.assertFalse(self.c.running)
        self.assertEqual(self.completed, [])

    def test_completion_before_drive_returns_is_buffered(self):
        self.arm(self.move())
        self.stage.during_request = lambda: self.c._on_sequence_completed(
            self.stage, self.sequence_event())
        self.c._run_next_step()
        self.assertEqual(self.completed, ["next"])

    def test_current_new_value_does_not_advance_until_finished(self):
        self.arm(self.c._custom("measure", "measure_faraday_cup"))
        self.c._run_next_step()
        self.c._on_beam_current_measurement(self.current_event(status.NewValue))
        self.assertEqual(self.completed, [])
        self.c._on_beam_current_measurement(self.current_event(status.Finished, 0))
        self.assertEqual(self.c.beam_current_in_a, 1.2e-9)
        self.assertEqual(self.completed, ["next"])

    def test_observed_trace_uses_fresh_value_and_named_cup_call(self):
        self.arm(self.c._custom("measure", "measure_faraday_cup"))
        self.c._run_next_step()
        for state in (status.Started, status.DrivingStage, status.TurnBeam, status.Measure):
            self.c._on_beam_current_measurement(self.current_event(state, 1.9e-9))
            self.assertIsNone(self.c.beam_current_in_a)
        new_value = 4.1971975e-11
        self.c._on_beam_current_measurement(self.current_event(status.NewValue, new_value))
        self.assertEqual(self.completed, [])
        self.c._on_beam_current_measurement(self.current_event(status.TurnBeam, new_value))
        self.c._on_beam_current_measurement(self.current_event(status.Finished, new_value))
        self.assertEqual(self.tm.measure_requests, [("Faraday Cup on USH", False)])
        self.assertEqual(self.c.beam_current_in_a, new_value)
        self.assertEqual(self.completed, ["next"])
        self.assertTrue(any("4.197197500e-11 A" in line for line in self.logs))

    def test_measurement_at_unexpected_location_does_not_advance(self):
        self.arm(self.c._custom("measure", "measure_faraday_cup"))
        self.c._run_next_step()
        event = self.current_event(status.NewValue)
        event.Position = "Nanorods"
        self.c._on_beam_current_measurement(event)
        self.assertFalse(self.c.running)
        self.assertIsNone(self.c.beam_current_in_a)
        self.assertEqual(self.completed, [])

    def test_current_text_preserves_subnanoamp_value(self):
        text = module["format_current_reading"](4.1971975e-11)
        self.assertEqual(text, "4.197197500e-11 A (0.041971975 nA)")

    def test_measurement_failure_prevents_save_or_next_operation(self):
        self.arm(self.c._custom("measure", "measure_faraday_cup"))
        self.c._run_next_step()
        self.c._on_beam_current_measurement(self.current_event(status.Failed))
        self.assertEqual(self.completed, [])
        self.assertFalse(self.c.running)

    def test_finished_without_fresh_value_does_not_advance_or_use_old_getter(self):
        self.arm(self.c._custom("measure", "measure_faraday_cup"))
        self.tm.BeamCurrentGetLastMeasureInA = lambda: self.fail("Stale getter must not establish freshness")
        self.c._run_next_step()
        self.c._on_beam_current_measurement(self.current_event(status.Finished, 0))
        self.assertFalse(self.c.running)
        self.assertEqual(self.completed, [])
        self.assertIsNone(self.c.beam_current_in_a)

    def test_rejected_measurement_does_not_wait(self):
        self.tm.accept_current = False
        self.arm(self.c._custom("measure", "measure_faraday_cup"))
        self.c._run_next_step()
        self.assertFalse(self.c.running)
        self.assertFalse(self.c.timer.active)

    def test_changed_saved_position_blocks_before_motion(self):
        self.stage.names[3] = "wrong location"
        self.c.start()
        self.assertEqual(self.stage.requests, [])
        self.assertFalse(self.c.running)

    def test_rejected_alignment_does_not_wait(self):
        self.tm.accept_alignment = False
        self.arm(self.c._alignment("focus", procedures.DynamicFocus))
        self.c._run_next_step()
        self.assertFalse(self.c.running)
        self.assertFalse(self.c.timer.active)

    def test_position_threshold_is_strict_and_stops_after_three(self):
        self.arm(self.c._alignment("final", procedures.DistortionUsingStage, 10.0))
        self.tm.result = types.SimpleNamespace(Procedure=procedures.DistortionUsingStage,
                                              DistortionFomInNM=10.0)
        self.c._run_next_step()
        for _ in range(3):
            self.c._on_component_status_changed(self.tm, types.SimpleNamespace(Status="Idle"))
        self.assertEqual(self.tm.starts, 3)
        self.assertEqual(self.completed, [])
        self.assertFalse(self.c.running)

    def test_timeout_prevents_sequence_advancement(self):
        self.arm(self.move())
        self.c._run_next_step()
        self.c._on_timeout(None, None)
        self.assertEqual(self.completed, [])
        self.assertFalse(self.c.running)

    def test_start_records_beam_mode_before_first_motion(self):
        self.c.start()
        self.assertEqual(self.c.save_target, ("test beam mode", None))
        self.assertEqual(self.stage.requests, ["Nanorods"])
        self.assertEqual(self.tm.save_calls, [])

    def test_unknown_beam_mode_prevents_first_motion(self):
        self.tm.mode = ""
        self.c.start()
        self.assertEqual(self.stage.requests, [])
        self.assertFalse(self.c.running)

    def arm_real_save_step(self):
        self.c.steps = self.c._build_steps()
        self.save_index = find_step(self.c.steps, "faraday_cup", "Save beam mode")
        self.c.step_index = self.save_index
        self.c.running = True
        self.c.sequence_state = "running"
        self.c.save_target = ("test beam mode", None)

    def test_successful_save_is_required_before_sample_move(self):
        self.arm_real_save_step()
        self.c._run_next_step()
        self.assertEqual(self.tm.save_calls, ["test beam mode"])
        self.assertEqual(self.c.saved_mode_name, "test beam mode")
        self.assertEqual(self.c.step_states[self.save_index], "done")
        self.assertEqual(self.stage.requests, ["sample"])
        self.assertEqual(self.c.step_index, find_step(self.c.steps, "sample", "Move to sample"))

    def test_false_save_result_prevents_sample_move(self):
        self.arm_real_save_step()
        self.tm.accept_save = False
        self.c._run_next_step()
        self.assertEqual(self.stage.requests, [])
        self.assertIsNone(self.c.saved_mode_name)
        self.assertFalse(self.c.running)

    def test_changed_mode_prevents_save_and_sample_move(self):
        self.arm_real_save_step()
        self.tm.mode = "another beam mode"
        self.c._run_next_step()
        self.assertEqual(self.tm.save_calls, [])
        self.assertEqual(self.stage.requests, [])
        self.assertFalse(self.c.running)

    def test_mode_change_during_save_prevents_sample_move(self):
        self.arm_real_save_step()
        self.tm.during_save = lambda: setattr(self.tm, "mode", "another beam mode")
        self.c._run_next_step()
        self.assertEqual(self.tm.save_calls, ["test beam mode"])
        self.assertEqual(self.stage.requests, [])
        self.assertIsNone(self.c.saved_mode_name)

    def test_save_exception_prevents_sample_move(self):
        self.arm_real_save_step()
        def fail():
            raise RuntimeError("simulated save failure")
        self.tm.during_save = fail
        self.c._run_next_step()
        self.assertEqual(self.stage.requests, [])
        self.assertFalse(self.c.running)

    def test_default_save_adapter_does_not_modify_callers_adapter_dictionary(self):
        original = {"record": lambda step, index: True}
        c = Controller(self.tm, types.SimpleNamespace(Output=self.logs.append), original, self.stage)
        self.assertNotIn("save_beam_calibration", original)
        self.assertIn("save_beam_calibration", c.adapters)


class DashboardBehavior(unittest.TestCase):
    def setUp(self):
        self.ui = DashboardUI()
        self.stage = Stage()
        self.tm = ToolManager()
        self.c = Controller(self.tm, self.ui, st=self.stage)
        self.c.steps = self.c._build_steps()
        self.ip = types.SimpleNamespace(ImageExists=lambda slot: slot == 17)
        self.view = Dashboard(self.c, self.ui, self.ip, preview_slot=17)
        self.ui.creation_locked = True

    def idx(self, phase, short_name):
        return find_step(self.c.steps, phase, short_name)

    def test_tracker_has_all_twenty_three_steps_and_only_setup_badges(self):
        self.assertEqual(len(self.view.rows), 23)
        self.assertTrue(self.view.rows[0].Text.startswith("[ ]"))
        self.assertTrue(self.view.rows[self.idx("faraday_cup", "Save beam mode")].Text.startswith("[ ]"))
        self.assertTrue(self.view.rows[self.idx("sample", "Burn three holes")].Text.startswith("[?]"))
        self.assertEqual(self.view.progress.Value, 0)
        self.assertFalse(self.view.stop_button.Enabled)

    def test_each_stigmator_is_immediately_followed_by_matching_focus(self):
        matching = {procedures.DynamicStigmator: procedures.DynamicFocus,
                    procedures.DynamicStigmatorFullField: procedures.DynamicFocusFullField}
        found = []
        for index, step in enumerate(self.c.steps):
            if step.get("procedure") in matching:
                after = self.c.steps[index + 1]
                self.assertEqual(after.get("procedure"), matching[step["procedure"]])
                self.assertEqual(after["phase"], step["phase"])
                self.assertIn("refocus", after["short_name"])
                found.append(index)
        self.assertEqual(len(found), 3)

    def test_refocus_must_finish_before_operator_confirmation(self):
        self.c._alignment_parameters = lambda procedure: "ParabolicAutoFocus"
        self.c.running = True
        self.c.sequence_state = "running"
        self.c.step_index = self.idx("nanorods", "Stigmator")
        self.c.attempt = 1
        self.c.waiting_for_alignment = True
        self.c.pending_operation = "alignment"
        self.tm.result = types.SimpleNamespace(Procedure=procedures.DynamicStigmator)
        self.c._on_component_status_changed(self.tm, types.SimpleNamespace(Status="Idle"))
        self.assertEqual(self.c.step_index, self.idx("nanorods", "Beam refocus"))
        self.assertEqual(self.tm.alignment_requests[-1][0], procedures.DynamicFocus)
        self.assertTrue(self.c.waiting_for_alignment)
        self.assertFalse(self.view.confirm_button.Enabled)
        self.tm.result = types.SimpleNamespace(Procedure=procedures.DynamicFocus)
        self.c._on_component_status_changed(self.tm, types.SimpleNamespace(Status="Idle"))
        self.assertEqual(self.c.step_index, self.idx("nanorods", "Confirm focus"))
        self.assertEqual(self.c.pending_operation, "confirm")
        self.assertTrue(self.view.confirm_button.Enabled)
        self.assertFalse(self.c.focus_confirmed)

    def test_refresh_never_submits_movement_alignment_or_image_requests(self):
        for _ in range(5):
            self.view.refresh()
        self.assertEqual(self.stage.requests, [])
        self.assertEqual(self.tm.starts, 0)
        self.assertEqual(self.ui.image_calls, [17])  # One prebuilt viewer, no further creation.

    def test_current_failed_step_preserves_completed_and_future_states(self):
        self.c.step_states = {0: "done", 1: "done", 2: "stopped"}
        self.c.step_index = 2
        self.c.sequence_state = "stopped"
        self.view.refresh()
        self.assertEqual(self.view.phase_widgets[0][1].Text, "STOPPED")
        self.assertEqual(self.view.phase_widgets[1][1].Text, "QUEUED")
        self.assertTrue(self.view.rows[0].Text.startswith("[OK]"))
        self.assertTrue(self.view.rows[2].Text.startswith("[STOP]"))
        self.assertEqual(self.view.progress.Value, int(200 / len(self.c.steps)))

    def test_save_without_recorded_start_target_stops(self):
        self.c.running = True
        self.c.sequence_state = "running"
        self.c.step_index = self.idx("faraday_cup", "Save beam mode")
        self.c._run_next_step()
        self.assertEqual(self.view.phase_widgets[2][1].Text, "STOPPED")
        self.assertIn("save target", self.view.status.Text)
        self.assertEqual(self.tm.save_calls, [])
        self.assertFalse(self.c.running)

    def test_results_show_measured_value_limit_and_site(self):
        coarse = self.idx("chessy", "Position coarse")
        final = self.idx("sample", "Position final")
        self.c.position_results[coarse] = dict(value=98.5, limit=100.0, passed=True, attempt=2)
        self.view.refresh()
        label = self.view.fom_labels[coarse]
        self.assertIn("Chessy", label.Text)
        self.assertIn("98.50 nm / <100 nm", label.Text)
        self.assertIn("try 2", label.Text)
        self.assertEqual(label.TextColor, "ForestGreen")
        self.assertIn("Sample", self.view.fom_labels[final].Text)
        self.assertIn("Pending", self.view.fom_labels[final].Text)

    def test_cached_image_labeled_previous_when_moving_to_another_site(self):
        self.c.running = True
        self.c.waiting_for_alignment = True
        self.c.step_index = 1
        self.c.attempt = 1
        self.c._on_automation_update(None, types.SimpleNamespace(SubProgress=42, ImageSlot=17))
        self.assertEqual(self.view.preview_source.Text, "Image received")
        self.assertEqual(self.view.preview_step, 1)
        self.c.step_index = self.idx("chessy", "Move to Chessy")
        self.c.waiting_for_alignment = False
        self.view.refresh()
        self.assertEqual(self.view.preview_source.Text, "Previous image")
        self.assertEqual(self.view.preview_source.TextColor, "DarkOrange")
        self.assertEqual(self.ui.image_calls, [17])
        self.assertEqual(self.stage.requests, [])
        self.assertEqual(self.tm.starts, 0)

    def test_previous_attempt_image_is_not_marked_current_during_retry(self):
        self.c.step_index = self.idx("chessy", "Position fine")
        self.c.attempt = 1
        self.view.show_existing_procedure_image(17, self.c.step_index)
        self.c.attempt = 2
        self.view.refresh()
        self.assertEqual(self.view.preview_source.Text, "Previous image")

    def test_operator_can_inspect_post_stigmator_refocus_image_for_confirmation(self):
        refocus = self.idx("nanorods", "Beam refocus")
        self.c.step_index = refocus
        self.c.attempt = 1
        self.view.show_existing_procedure_image(17, refocus)
        self.c.step_index = self.idx("nanorods", "Confirm focus")
        self.view.refresh()
        self.assertEqual(self.view.preview_source.Text, "Image received")
        self.assertEqual(self.view.preview_step, refocus)

    def test_preview_does_not_require_an_image_processor(self):
        ui = DashboardUI()
        view = Dashboard(self.c, ui, None, preview_slot=17)
        view.show_existing_procedure_image(17, 1)
        self.assertIsNone(view.image)
        self.assertEqual(ui.image_calls, [])

    def test_dashboard_controls_fit_and_leave_room_for_host_exit_button(self):
        for control in self.ui.controls:
            if control.parent is not None:
                self.assertLessEqual(control.Left + control.Width, control.parent.Width)
                self.assertLessEqual(control.Top + control.Height, control.parent.Height)
            else:
                self.assertLessEqual(control.Left + control.Width, module["GUI_WINDOW_WIDTH"])
                self.assertLessEqual(control.Top + control.Height,
                                     module["GUI_WINDOW_HEIGHT"] - module["GUI_EXIT_CLEARANCE"])

    def test_compact_dashboard_fits_screenshot_and_1024_desktop(self):
        for work_width, work_height in ((1035, 775), (1024, 728), (1920, 1040)):
            width, height, left, top = module["dashboard_window_spec"]((0, 0, work_width, work_height))
            self.assertLessEqual(left + width + 22, work_width)
            self.assertLessEqual(top + height + 40, work_height)
            for control in self.ui.controls:
                if control.parent is None:
                    self.assertLessEqual(control.Left + control.Width, width - 12)
                    self.assertLessEqual(control.Top + control.Height,
                                         height - module["GUI_EXIT_CLEARANCE"])

    def test_phase_titles_do_not_show_position_list_indexes(self):
        for box, state_label, count_label, indexes in self.view.phase_widgets:
            self.assertNotRegex(box.Text, r"\[\d+\]")
        self.assertEqual(module["POSITION_LIST_INDEXES"],
                         dict(nanorods=3, faraday_cup=2, chessy=4, sample=5))

    def test_viewer_is_created_at_compact_size_before_run(self):
        self.assertEqual((self.view.image.Width, self.view.image.Height), (224, 224))
        for _ in range(3):
            self.view.refresh()
        self.assertEqual(self.ui.image_calls, [17])

    def test_position_turns_green_only_after_matching_successful_move(self):
        self.c._alignment_parameters = lambda procedure: "ParabolicAutoFocus"
        self.c.start()
        self.assertEqual(self.view.position.Text, "Moving to Nanorods")
        self.assertEqual(self.view.position.TextColor, "Firebrick")
        self.c._on_sequence_completed(self.stage, types.SimpleNamespace(Id=99, Success=True))
        self.assertEqual(self.view.position.TextColor, "Firebrick")
        self.c._on_sequence_completed(self.stage, types.SimpleNamespace(Id=42, Success=True))
        self.assertEqual(self.view.position.Text, "Nanorods")
        self.assertEqual(self.view.position.TextColor, "ForestGreen")
        self.assertNotIn("telemetry", self.view.position.Text)

    def test_named_measurement_position_feedback_and_value_display(self):
        self.c.running = True
        self.c.pending_operation = "current"
        self.c.step_index = self.idx("faraday_cup", "Measure current")
        event = types.SimpleNamespace(Status=status.DrivingStage, BeamCurrentInA=1.9e-9,
                                      Position="Faraday Cup on USH", DriveBack=False, Comment="simulation")
        self.c._on_beam_current_measurement(event)
        self.assertEqual(self.view.position.TextColor, "Firebrick")
        event.Status = status.Measure
        self.c._on_beam_current_measurement(event)
        self.assertEqual(self.view.position.TextColor, "ForestGreen")
        event.Status, event.BeamCurrentInA = status.NewValue, 4.1971975e-11
        self.c._on_beam_current_measurement(event)
        self.assertIn("0.041971975 nA", self.view.current.Text)

    def test_focus_panel_has_no_wd_feedback_or_event_subscription(self):
        self.c.cc = types.SimpleNamespace(OnWorkingDistanceChanged=Event())
        self.c.start()
        self.assertEqual(self.c.cc.OnWorkingDistanceChanged.handlers, [])
        self.assertNotIn("WD", self.view.focus.Text)
        self.assertNotIn("readback", self.view.focus.Text)
        self.assertIn("Parabolic autofocus", self.view.focus.Text)

    def test_images_are_shown_for_each_focus_phase(self):
        self.c.running = True
        self.c.waiting_for_alignment = True
        self.c.attempt = 1
        focus_indexes = [i for i, step in enumerate(self.c.steps)
                         if step.get("procedure") in (procedures.DynamicFocus, procedures.DynamicFocusFullField)]
        for index in focus_indexes:
            self.c.step_index = index
            self.c._on_automation_update(None, types.SimpleNamespace(SubProgress=40, ImageSlot=17))
            self.assertEqual(self.view.preview_step, index)
            self.assertEqual(self.view.preview_source.Text, "Image received")
        self.assertEqual(self.ui.image_calls, [17])
        self.assertEqual(self.tm.starts, 0)

    def test_late_focus_image_can_arrive_during_operator_confirmation(self):
        self.c.running = True
        self.c.waiting_for_alignment = False
        self.c.step_index = self.idx("nanorods", "Confirm focus")
        self.c.attempt = 1
        self.c._on_automation_update(None, types.SimpleNamespace(SubProgress=100, ImageSlot=17))
        self.assertEqual(self.view.preview_step, self.idx("nanorods", "Beam refocus"))
        self.assertEqual(self.view.preview_source.Text, "Image received")

    def test_focus_confirmation_waits_for_button_without_creating_dialogs(self):
        self.c._alignment_parameters = lambda procedure: "ParabolicAutoFocus"
        self.c.running = True
        self.c.sequence_state = "running"
        confirm = self.idx("nanorods", "Confirm focus")
        after_confirm = self.idx("nanorods", "Field focus")
        self.c.step_index = confirm
        self.c._run_next_step()
        self.assertEqual(self.c.pending_operation, "confirm")
        self.assertFalse(self.c.focus_confirmed)
        self.assertEqual(self.c.step_index, confirm)
        self.assertTrue(self.view.confirm_button.Enabled)
        self.assertEqual(self.tm.starts, 0)
        self.c.confirm_focus()
        self.assertTrue(self.c.focus_confirmed)
        self.assertEqual(self.c.step_index, after_confirm)
        self.assertFalse(self.view.confirm_button.Enabled)
        self.assertEqual(self.tm.starts, 1)
        self.c.confirm_focus()  # Duplicate click cannot skip the next step.
        self.assertEqual(self.c.step_index, after_confirm)
        self.assertEqual(self.tm.starts, 1)

    def test_focus_cannot_be_confirmed_after_stop(self):
        self.c.running = True
        self.c.sequence_state = "running"
        confirm = self.idx("nanorods", "Confirm focus")
        self.c.step_index = confirm
        self.c._run_next_step()
        self.c.stop("operator stopped")
        self.c.confirm_focus()
        self.assertFalse(self.c.focus_confirmed)
        self.assertEqual(self.c.step_index, confirm)
        self.assertFalse(self.view.confirm_button.Enabled)

    def test_image_control_is_prebuilt_and_receives_only_slot_updates(self):
        original = self.view.image
        self.c.running = True
        self.c.waiting_for_alignment = True
        self.c.step_index = 1
        self.c.attempt = 1
        self.c._on_automation_update(None, types.SimpleNamespace(SubProgress=10, ImageSlot=17))
        self.view.refresh()
        self.assertIs(self.view.image, original)
        self.assertEqual(self.view.image.SlotOrChannelNumber, 17)
        self.assertEqual(self.ui.image_calls, [17])

    def test_frame_from_previous_run_is_not_marked_current(self):
        self.c.step_index = 1
        self.c.attempt = 1
        self.view.show_existing_procedure_image(17, 1)
        self.c.start()
        self.c.step_index = 1
        self.c.attempt = 1
        self.view.refresh()
        self.assertEqual(self.view.preview_source.Text, "Previous image")
        self.assertIsNone(self.view.preview_step)
        self.assertEqual(self.ui.image_calls, [17])

    def test_reference_slot_can_be_resolved_from_commonlib_assembly(self):
        domain = module["resolve_preview_reference_slot"].__globals__["AppDomain"].CurrentDomain
        previous = domain.GetAssemblies
        field = types.SimpleNamespace(GetValue=lambda instance: 17)
        reserved = types.SimpleNamespace(Name="ReservedImageSlots", GetField=lambda name: field)
        assembly = types.SimpleNamespace(GetTypes=lambda: [reserved])
        domain.GetAssemblies = lambda: [assembly]
        try:
            self.assertEqual(module["resolve_preview_reference_slot"](), 17)
        finally:
            domain.GetAssemblies = previous

    def test_viewer_links_to_overview_using_supplied_fifth_argument(self):
        ui = DashboardUI()
        ip = types.SimpleNamespace(ImageExists=lambda slot: slot in (17, 18, 19))
        view = Dashboard(self.c, ui, ip, preview_slot=17, overview_slot=18)
        self.assertEqual(ui.image_calls, [18, 17, 17])
        self.assertIs(ui.image_links[1], view.overview_image)
        self.assertEqual(view.reference_image.SlotOrChannelNumber, 17)
        ui.creation_locked = True
        self.c.step_index = 1
        self.c.attempt = 1
        view.show_existing_procedure_image(19, 1)
        self.assertEqual(view.image.SlotOrChannelNumber, 19)
        self.assertEqual(view.overview_image.SlotOrChannelNumber, 18)
        self.assertEqual(view.reference_image.SlotOrChannelNumber, 17)
        self.assertEqual(view.preview_source.Text, "Image received")
        self.assertEqual(ui.image_calls, [18, 17, 17])

    def test_linked_images_fit_inside_existing_compact_image_panel(self):
        view = Dashboard(self.c, DashboardUI(), self.ip, preview_slot=17, overview_slot=18)
        left, top, width, height = view.preview_region
        for image in (view.image, view.overview_image, view.reference_image):
            self.assertGreaterEqual(image.Left, left)
            self.assertGreaterEqual(image.Top, top)
            self.assertLessEqual(image.Left + image.Width, left + width)
            self.assertLessEqual(image.Top + image.Height, top + height)

    def test_no_native_control_covers_any_image_rectangle(self):
        ui = DashboardUI()
        view = Dashboard(self.c, ui, self.ip, preview_slot=17, overview_slot=18)
        images = (view.image, view.overview_image, view.reference_image)

        def bounds(control):
            x, y = control.Left, control.Top
            parent = control.parent
            while parent is not None:
                x += parent.Left
                y += parent.Top
                parent = parent.parent
            return x, y, x + control.Width, y + control.Height

        for image in images:
            x1, y1, x2, y2 = bounds(image)
            for control in ui.controls:
                if control in images:
                    continue
                a1, b1, a2, b2 = bounds(control)
                overlaps = min(x2, a2) > max(x1, a1) and min(y2, b2) > max(y1, b1)
                self.assertFalse(overlaps, "Control covers image: " + control.Text)

    def test_successful_slot_assignment_logs_data_receipt_once(self):
        self.c.step_index = 1
        self.c.attempt = 1
        for _ in range(3):
            self.view.show_existing_procedure_image(17, 1)
        receipts = [line for line in self.ui.logs if "Image data in IP:" in line]
        self.assertEqual(len(receipts), 1)
        self.assertIn("slot 17", receipts[0])

    def test_existing_reference_is_displayed_without_starting_calibration(self):
        self.assertEqual(self.view.image.SlotOrChannelNumber, 17)
        self.assertEqual(self.view.preview_source.Text, "Previous image")
        self.assertIsNone(self.view.preview_step)
        self.assertFalse(self.c.running)
        self.assertEqual(self.tm.starts, 0)
        self.assertEqual(self.stage.requests, [])


class EmulatedImageSetup(unittest.TestCase):
    def make_pg(self, emulated):
        calls = []
        mode_type = types.SimpleNamespace(IsEnum=True, SimulatedImageWithNoise="simulated_noise")
        pattern_type = types.SimpleNamespace(IsEnum=True, Feature="feature")
        methods = [types.SimpleNamespace(Name=name, GetParameters=lambda enum_type=enum_type: [
            types.SimpleNamespace(ParameterType=enum_type)])
            for name, enum_type in (("EnableLoopback", mode_type), ("SetLoopbackPattern", pattern_type))]
        pg = types.SimpleNamespace(IsInEmulationMode=lambda: emulated,
            GetType=lambda: types.SimpleNamespace(GetMethods=lambda: methods),
            EnableLoopback=lambda mode: calls.append(("loopback", mode)),
            SetLoopbackPattern=lambda pattern: calls.append(("pattern", pattern)))
        return pg, calls

    def test_non_emulated_generator_is_not_reconfigured(self):
        pg, calls = self.make_pg(False)
        self.assertFalse(module["configure_emulated_images"](pg))
        self.assertEqual(calls, [])

    def test_emulated_generator_uses_original_feature_image_setup(self):
        pg, calls = self.make_pg(True)
        self.assertTrue(module["configure_emulated_images"](pg))
        self.assertEqual(calls, [("loopback", "simulated_noise"), ("pattern", "feature")])

    def test_absent_generator_requires_no_setup(self):
        self.assertFalse(module["configure_emulated_images"](None))


if __name__ == "__main__":
    unittest.main()
