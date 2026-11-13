# Raith Automatic Beam Calibration orchestration
#
# Intended to run in the Raith IronPython scripting environment where TM, ST,
# UI, AlignmentProcedure, ComponentStatus, and the project's position-list
# selection are supplied by the host application.
#
# Verified from the supplied AutomaticAlignment script:
#   TM.StartAutomaticAlignment(procedure, parameters)
#   TM.GetLastAlignmentResult()
#   result.Procedure and result.DistortionFomInNM for the reported position FOM
#   TM.OnComponentStatusChanged and ComponentStatus.Idle
#   AlignmentProcedure values used below (including DistortionUsingStage)
#   ST.OnStagePositionChanged with e.X/e.Y for displayed stage coordinates
#
# Stored-position movement and current measurement now use verified runtime
# signatures. Drive-name and returned sequence-ID semantics require the first
# sandbox movement check. The user confirmed the beam-mode save check works.
# Hole exposure and the stage-Z loop remain explicit integration points.
# Loading in the Raith host opens a Start/Stop
# window; no instrument actions are submitted until Start is pressed.

import math
from System import AppDomain, Enum as ClrEnum
from System.Drawing import Color
from System.Windows.Forms import Timer, Screen
from Raith.CommonLib import (AlignmentProcedure, ComponentStatus,
                            WorkingDistanceParameterKind, BeamCurrentMeasurementStatus)


POSITION_THRESHOLDS_NM = {
    "Position Coarse": 100.0,
    "Position Fine": 50.0,
    "Position Final": 10.0,
}

# Runtime list enumeration matches these user-supplied zero-based indexes.
# DriveStoredPosition takes a string; validate the list label before submitting
# the name below. Acceptance of display names as drive names still requires a
# first sandbox movement check; reflection verifies types, not that behavior.

POSITION_LIST_INDEXES = {
    "nanorods": 3,           # TODO: confirm exact list index
    "faraday_cup": 2,
    "chessy": 4,             # TODO: confirm exact list index
    "sample": 5,             # TODO: confirm exact list index
}

EXPECTED_POSITION_NAMES = {
    "nanorods": "Nanorods",
    "faraday_cup": "Faraday Cup on USH",
    "chessy": "Chessy",
    "sample": "sample",
}

# Override a name here if the drive API requires a different internal name.
DRIVE_POSITION_NAMES = dict(EXPECTED_POSITION_NAMES)

# Chosen watchdog duration, not an instrument specification. Adjust to the
# site's observed operation duration. Expiry stops sequence advancement.
OPERATION_TIMEOUT_MS = 300000

MAX_ATTEMPTS = 3
TARGET_WORKING_DISTANCE_MM = 20.000
WORKING_DISTANCE_TOLERANCE_MM = 0.005

# These options and values come from the supplied alignment example. Need to confirm
# whether they are appropriate for every procedure.
DISTORTION_FOM_SETTING = 7.0
POSITION_FOM_SETTING = 5.0
USE_STANDARD_SETTINGS = True
PRESET_FIELD_ROTATION = True
MINIMUM_SETTINGS = False

# Display metadata follows the actual execution order; no extra procedures or
# image acquisitions are added by the dashboard.
PHASE_LAYOUT = (
    ("nanorods", "Nanorods", ("Move to Nanorods", "Beam focus", "Stigmator",
                                  "Beam refocus", "Confirm focus", "Field focus",
                                  "Field stigmator", "Field refocus")),
    ("chessy", "Chessy", ("Move to Chessy", "Beam focus", "Stigmator",
                              "Beam refocus", "Position coarse", "Position fine", "Position final")),
    ("faraday_cup", "Faraday Cup", ("Move to cup", "Measure current", "Save beam mode")),
    ("sample", "Sample", ("Move to sample", "Burn three holes", "Beam focus",
                              "Set Z / working distance", "Position final")),
)

# Compact logical-pixel layout, including space for Raith's automatic Exit row.
# Size controls before ShowModal; avoid resizing/reparenting remote image controls.
GUI_WINDOW_WIDTH = 960
GUI_WINDOW_HEIGHT = 700
GUI_EXIT_CLEARANCE = 50


def format_current_reading(value_a):
    # Explicit scientific notation avoids IronPython's observed .format(...g)
    # rounding of sub-nanoampere values to the misleading text '0 A'.
    value_a = float(value_a)
    return "%.9e A (%.9f nA)" % (value_a, value_a * 1e9)


class CalibrationStopped(Exception):
    pass


class AutomaticBeamCalibration(object):
    """Event-driven sequence runner for known ToolManager alignment calls."""

    def __init__(self, tm, ui, adapters=None, st=None, cc=None):
        self.tm = tm
        self.ui = ui
        self.adapters = dict(adapters or {})
        self.st = st if st is not None else globals().get("ST")
        self.cc = cc if cc is not None else globals().get("CC")
        self.steps = []
        self.step_index = 0
        self.attempt = 0
        self.waiting_for_alignment = False
        self.running = False
        self.sample_position_index = None
        self.pending_operation = None
        self.expected_sequence_id = None
        self.starting_request = False
        self.early_events = []
        self.beam_current_in_a = None
        self.events_attached = False
        self.status_label = None
        self.status_message = "Ready. No sequence started."
        self.sequence_state = "ready"
        self.step_states = {}
        self.position_results = {}
        self.position_name = "Not requested"
        self.position_state = "Ready"
        self.focus_confirmed = False
        self.current_status = "Not measured"
        self.save_target = None
        self.saved_mode_name = None
        self.subprogress = 0
        self.view = None
        self._optional_events = []
        self.timer = Timer()
        self.timer.Interval = OPERATION_TIMEOUT_MS
        self.timer.Tick += self._on_timeout
        self._on_status_handler = self._on_component_status_changed
        self._on_sequence_handler = self._on_sequence_completed
        self._on_current_handler = self._on_beam_current_measurement
        self.adapters.setdefault("save_beam_calibration", self.save_current_settings)

    def start(self):
        """Start using the configured indexes into the saved position list."""
        if self.running:
            self._report("Calibration is already running.")
            return
        missing = [role for role in ("nanorods", "chessy", "faraday_cup", "sample")
                   if POSITION_LIST_INDEXES.get(role) is None]
        if missing:
            self._report("Set the confirmed position-list index(es) first: {0}".format(
                ", ".join(missing)))
            return

        if self.st is None:
            self._report("Stage client ST is required.")
            return
        try:
            names = list(self.st.GetStoredPositionsDisplayNameList())
            for role, index in POSITION_LIST_INDEXES.items():
                if index < 0 or index >= len(names):
                    raise ValueError("Stored-position index out of range: " + role)
                if str(names[index]) != EXPECTED_POSITION_NAMES[role]:
                    raise ValueError("Index {0} for {1} now contains '{2}', expected '{3}'.".format(
                        index, role, names[index], EXPECTED_POSITION_NAMES[role]))
            save_target = self._read_beam_mode_identity()
            self.tm.OnComponentStatusChanged += self._on_status_handler
            self.st.OnSequenceCompleted += self._on_sequence_handler
            # This delegate takes ONE event argument, not sender plus event.
            self.tm.OnBeamCurrentMeasurement += self._on_current_handler
            self.events_attached = True
        except Exception as ex:
            self._detach_events()
            self._report("Could not prepare calibration: {0}".format(ex))
            return

        # This subscription consumes images already produced by focus,
        # stigmator and position procedures; it never requests an acquisition.
        # Their absence must not prevent the calibration's required events.
        self._attach_optional_event(self.tm, "OnAutomationUpdate", self._on_automation_update)

        self.sample_position_index = POSITION_LIST_INDEXES["sample"]
        self.save_target = save_target
        self.saved_mode_name = None
        self.steps = self._build_steps()
        self.step_index = 0
        self.attempt = 0
        self.waiting_for_alignment = False
        self.running = True
        self.sequence_state = "running"
        self.step_states = {}
        self.position_results = {}
        self.focus_confirmed = False
        self.beam_current_in_a = None
        self.current_status = "Not measured"
        if self.view is not None:
            self.view.reset_image_receipt()
        self._report("Starting beam calibration using saved position-list indexes: {0}".format(
            POSITION_LIST_INDEXES))
        self._report("Beam mode to save after calibration: '{0}'.".format(self.save_target[0]))
        self._run_next_step()

    def stop(self, reason):
        active_operation = self.pending_operation
        if self.waiting_for_alignment:
            try:
                self.tm.StopAutomaticAlignment()
            except:
                pass
        self.running = False
        self.sequence_state = "stopped"
        if self.step_index < len(self.steps) and self.step_states.get(self.step_index) != "blocked":
            self.step_states[self.step_index] = "stopped"
        self.waiting_for_alignment = False
        self.pending_operation = None
        self.timer.Stop()
        self._detach_events()
        message = "STOPPED: {0}".format(reason)
        if active_operation in ("move", "current"):
            message += " That instrument operation may still be active; use its native stop control if needed."
        self._report(message)

    def _detach_events(self):
        for event, handler in self._optional_events:
            try:
                event -= handler
            except Exception:
                pass
        self._optional_events = []
        # Also handles partial subscription if setup fails.
        for obj, event_name, handler in (
                (self.tm, "OnComponentStatusChanged", self._on_status_handler),
                (self.st, "OnSequenceCompleted", self._on_sequence_handler),
                (self.tm, "OnBeamCurrentMeasurement", self._on_current_handler)):
            if obj is not None:
                try:
                    event = getattr(obj, event_name)
                    event -= handler
                except Exception:
                    pass
        self.events_attached = False

    def _attach_optional_event(self, obj, name, handler):
        try:
            event = getattr(obj, name)
            event += handler
            self._optional_events.append((event, handler))
        except Exception:
            pass

    def _notify_view(self):
        if self.view is not None:
            try:
                self.view.refresh()
            except Exception as ex:
                # A display problem must never advance an operation.
                try:
                    self.ui.Output("Dashboard display error: {0}\n".format(ex))
                except Exception:
                    pass

    def _on_automation_update(self, sender, e):
        if not self.running or self.step_index >= len(self.steps):
            return
        step = self.steps[self.step_index]
        if self.waiting_for_alignment or step.get("adapter") == "adjust_stage_z_to_working_distance":
            image_step = self.step_index
        elif step.get("adapter") == "operator_confirm_focus" and self.step_index > 0:
            # A focus/stigmator image can arrive just after Idle, while the
            # operator confirmation is displayed. Keep that image visible.
            image_step = self.step_index - 1
        else:
            return
        self.subprogress = max(0, min(100, int(e.SubProgress)))
        if self.view is not None:
            try:
                self.view.show_existing_procedure_image(e.ImageSlot, image_step)
            except Exception:
                pass  # Optional preview must not interrupt alignment.
        self._notify_view()

    def _on_timeout(self, sender, e):
        self.timer.Stop()
        if self.running:
            self.stop("No completion received within {0} seconds for {1}.".format(
                OPERATION_TIMEOUT_MS // 1000, self.steps[self.step_index]["name"]))

    def _advance(self):
        self.timer.Stop()
        self.pending_operation = None
        self.step_states[self.step_index] = "done"
        self.step_index += 1
        self._run_next_step()

    def _build_steps(self):
        steps = [
            self._custom("Move to saved Nanorods position", "move_to_position_list_index",
                         position_role="nanorods",
                         position_list_index=POSITION_LIST_INDEXES["nanorods"]),
            self._alignment("Nanorods: Beam Focus", AlignmentProcedure.DynamicFocus),
            self._alignment("Nanorods: Stig Focus", AlignmentProcedure.DynamicStigmator),
            self._alignment("Nanorods: Beam Focus after stigmator", AlignmentProcedure.DynamicFocus),
            self._custom("Operator confirms Nanorods focus before continuing",
                         "operator_confirm_focus"),

            self._alignment("Nanorods: Field Focus", AlignmentProcedure.DynamicFocusFullField),
            self._alignment("Nanorods: Field Stig Focus", AlignmentProcedure.DynamicStigmatorFullField),
            self._alignment("Nanorods: Field Focus after stigmator", AlignmentProcedure.DynamicFocusFullField),
            self._custom("Move to saved Chessy position", "move_to_position_list_index",
                         position_role="chessy",
                         position_list_index=POSITION_LIST_INDEXES["chessy"]),

            self._alignment("Chessy: Beam Focus", AlignmentProcedure.DynamicFocus),
            self._alignment("Chessy: Stig Focus", AlignmentProcedure.DynamicStigmator),
            self._alignment("Chessy: Beam Focus after stigmator", AlignmentProcedure.DynamicFocus),
            self._alignment("Position Coarse", AlignmentProcedure.PositionCoarse, 100.0),
            self._alignment("Position Fine", AlignmentProcedure.PositionFine, 50.0),
            # The supplied GUI labels this button Position Final but assigns
            # AlignmentProcedure.DistortionUsingStage.
            self._alignment("Position Final", AlignmentProcedure.DistortionUsingStage, 10.0),

            self._custom("Move to saved Faraday Cup position", "move_to_position_list_index",
                         position_role="faraday_cup",
                         position_list_index=POSITION_LIST_INDEXES["faraday_cup"]),
            self._custom("Measure Faraday cup", "measure_faraday_cup",
                         position_list_index=POSITION_LIST_INDEXES["faraday_cup"]),
            self._custom("Save active beam mode", "save_beam_calibration"),

            self._custom("Move to saved Sample position", "move_to_position_list_index",
                         position_role="sample",
                         position_list_index=POSITION_LIST_INDEXES["sample"]),
            self._custom("Expose three holes at sample (not assumed pre-burned)",
                         "expose_three_holes",
                         position_list_index=POSITION_LIST_INDEXES["sample"]),
            self._alignment("Sample: Beam Focus", AlignmentProcedure.DynamicFocus),
            self._custom("Adjust stage Z until focus WD is 20.000 +/- 0.005 mm",
                         "adjust_stage_z_to_working_distance",
                         target_mm=TARGET_WORKING_DISTANCE_MM,
                         tolerance_mm=WORKING_DISTANCE_TOLERANCE_MM,
                         max_attempts=MAX_ATTEMPTS),
            self._alignment("Sample: Position Final", AlignmentProcedure.DistortionUsingStage, 10.0),
        ]
        index = 0
        for role, title, labels in PHASE_LAYOUT:
            for label in labels:
                steps[index]["phase"] = role
                steps[index]["short_name"] = label
                index += 1
        if index != len(steps):
            raise ValueError("Dashboard steps do not match execution steps.")
        return steps

    def _alignment(self, name, procedure, threshold=None):
        return {"kind": "alignment", "name": name, "procedure": procedure,
                "threshold": threshold}

    def _custom(self, name, adapter_name, **options):
        step = {"kind": "custom", "name": name, "adapter": adapter_name}
        step.update(options)
        return step

    def _run_next_step(self):
        if not self.running:
            return
        if self.step_index >= len(self.steps):
            self.running = False
            self.sequence_state = "completed"
            self.timer.Stop()
            self._detach_events()
            self._report("Calibration sequence completed.")
            return

        step = self.steps[self.step_index]
        self.step_states[self.step_index] = "active"
        self.subprogress = 0
        self.attempt = 1
        self._report("Step {0}/{1}: {2}".format(self.step_index + 1, len(self.steps), step["name"]))
        if step["kind"] == "alignment":
            self._start_alignment(step)
        else:
            self._run_custom_step(step)

    def _start_alignment(self, step):
        try:
            parameters = self._alignment_parameters(step["procedure"])
        except Exception as ex:
            self.stop("Could not construct alignment parameters: {0}".format(ex))
            return
        self.waiting_for_alignment = True
        self.pending_operation = "alignment"
        self.timer.Start()
        self._report("{0} (attempt {1}/{2})".format(
            step["name"], self.attempt, MAX_ATTEMPTS))
        try:
            accepted = self.tm.StartAutomaticAlignment(step["procedure"], parameters)
            if not accepted:
                self.waiting_for_alignment = False
                self.stop("ToolManager rejected the start request for {0}.".format(step["name"]))
        except Exception as ex:
            self.stop("Could not start {0}: {1}".format(step["name"], ex))

    def _alignment_parameters(self, procedure):
        parameters = ""
        if USE_STANDARD_SETTINGS and not AD.ReadSysBool("RSServer.Enabled", False):
            parameters += "UseSettings=Standard"
        parameters += ", ImprovedFocusMap"
        if PRESET_FIELD_ROTATION:
            parameters += ", PresetFieldRotation"
        if MINIMUM_SETTINGS:
            parameters += ", MinimumSettings"
        parameters += ", EnableLogging"
        parameters += ", ParabolicAutoFocus"
        if procedure == AlignmentProcedure.DynamicStigmatorFullField:
            parameters += ", NumberOfCoefficients=6"
        parameters += ", DistortionFOM=" + str(DISTORTION_FOM_SETTING)
        parameters += ", PositionFOM=" + str(POSITION_FOM_SETTING)
        return parameters

    def _on_component_status_changed(self, sender, e):
        if not self.running or not self.waiting_for_alignment:
            return
        if e.Status != ComponentStatus.Idle:
            return

        self.timer.Stop()
        self.waiting_for_alignment = False
        self.pending_operation = None
        step = self.steps[self.step_index]
        try:
            result = self.tm.GetLastAlignmentResult()
        except Exception as ex:
            self.stop("Could not read the result for {0}: {1}".format(step["name"], ex))
            return

        if result is None:
            self.stop("ToolManager returned no result for {0}.".format(step["name"]))
            return
        if result.Procedure != step["procedure"]:
            self.stop("Result procedure did not match {0}; received {1}.".format(
                step["name"], result.Procedure))
            return

        threshold = step["threshold"]
        if threshold is not None:
            try:
                fom_nm = float(result.DistortionFomInNM)
            except Exception as ex:
                self.stop("Could not read DistortionFomInNM for {0}: {1}".format(
                    step["name"], ex))
                return
            self._report("{0} reported FOM: {1:.2f} nm (required < {2:.2f})".format(
                step["name"], fom_nm, threshold))
            self.position_results[self.step_index] = {
                "value": fom_nm, "limit": threshold, "passed": fom_nm < threshold,
                "attempt": self.attempt}
            self._notify_view()
            if not (fom_nm < threshold):
                if self.attempt < MAX_ATTEMPTS:
                    self.attempt += 1
                    self._start_alignment(step)
                    return
                self.stop("{0} did not meet its reported FOM threshold after {1} attempts.".format(
                    step["name"], MAX_ATTEMPTS))
                return
        else:
            # Focus relies on ParabolicAutoFocus. No focus FOM is checked.
            self._report("{0} completed; no focus FOM check is used.".format(step["name"]))

        self._advance()

    def _run_custom_step(self, step):
        if step["adapter"] == "move_to_position_list_index":
            self._start_stored_position_move(step)
            return
        if step["adapter"] == "measure_faraday_cup":
            self._start_beam_current_measurement()
            return
        if step["adapter"] == "operator_confirm_focus":
            self.step_states[self.step_index] = "confirm"
            self.pending_operation = "confirm"
            self._report("Inspect the focus image, then click Confirm focus and continue. Use Stop sequence if focus is incorrect.")
            return

        callback = self.adapters.get(step["adapter"])
        if callback is None:
            self.step_states[self.step_index] = "blocked"
            self.stop("Missing required adapter '{0}' for step: {1}.".format(
                step["adapter"], step["name"]))
            return
        try:
            outcome = callback(step, step.get("position_list_index"))
        except Exception as ex:
            self.stop("Step '{0}' failed: {1}".format(step["name"], ex))
            return
        if outcome is not True:
            self.stop("Step '{0}' did not explicitly confirm success.".format(step["name"]))
            return
        self._report("Completed: {0}".format(step["name"]))
        self._advance()

    def confirm_focus(self):
        # Non-modal confirmation: no UI controls or dialogs are created from
        # an instrument callback. Ignore late or duplicate confirmation clicks.
        if not self.running or self.pending_operation != "confirm":
            return
        if (self.step_index >= len(self.steps) or
                self.steps[self.step_index].get("adapter") != "operator_confirm_focus"):
            return
        self.focus_confirmed = True
        self._report("Operator confirmed focus.")
        self._advance()

    def _start_stored_position_move(self, step):
        role = step["position_role"]
        index = step["position_list_index"]
        self.position_name = EXPECTED_POSITION_NAMES[role]
        try:
            names = list(self.st.GetStoredPositionsDisplayNameList())
            if str(names[index]) != EXPECTED_POSITION_NAMES[role]:
                raise ValueError("Stored-position list changed since Start.")
            drive_name = DRIVE_POSITION_NAMES[role]
            self.position_state = "Driving"
            self.pending_operation = "move"
            self.expected_sequence_id = None
            self.early_events = []
            self.starting_request = True
            self.timer.Start()
            self._report("Driving to {0}: index {1}, name '{2}'.".format(role, index, drive_name))
            # Runtime verifies the String argument and Int32 return type.
            # ASSUMPTION awaiting a sandbox check: this return is a sequence ID,
            # matching OnSequenceCompleted.e.Id. An unmatched event never advances.
            self.expected_sequence_id = int(self.st.DriveStoredPosition(drive_name))
            self._report("Move request returned {0}; waiting for matching sequence completion.".format(
                self.expected_sequence_id))
        except Exception as ex:
            self.position_state = "Move failed"
            self.stop("Stored-position move request failed: {0}".format(ex))
            return
        finally:
            self.starting_request = False
        buffered = self.early_events
        self.early_events = []
        for event in buffered:
            if self.running and self.pending_operation == "move":
                self._on_sequence_completed(self.st, event)

    def _on_sequence_completed(self, sender, e):
        if not self.running or self.pending_operation != "move":
            return
        if self.starting_request:
            self.early_events.append(e)
            return
        if int(e.Id) != self.expected_sequence_id:
            self._report("Ignoring other sequence completion {0}; waiting for {1}.".format(
                e.Id, self.expected_sequence_id))
            return
        if not e.Success:
            self.position_state = "Move failed"
            self.stop("Stage sequence {0} failed: L={1}, X={2}, Y={3}, Z={4}, R={5}, T={6}.".format(
                e.Id, e.ErrorL, e.ErrorX, e.ErrorY, e.ErrorZ, e.ErrorR, e.ErrorT))
            return
        self._report("Stage sequence {0} completed successfully.".format(e.Id))
        self.position_state = "Reached (stage controller)"
        self._advance()

    def _start_beam_current_measurement(self):
        self.pending_operation = "current"
        self.beam_current_in_a = None
        self.current_status = "Starting measurement"
        self.early_events = []
        self.starting_request = True
        self.timer.Start()
        try:
            # This named-position call was exercised in the user's sandbox:
            # Started -> DrivingStage -> TurnBeam -> Measure -> NewValue ->
            # TurnBeam -> Finished. False was reported as driveBack=0.
            # The separate move step is retained. TM may position/check the
            # same cup again as part of its own measurement procedure.
            accepted = self.tm.BeamCurrentStartMeasureAtPos(
                DRIVE_POSITION_NAMES["faraday_cup"], False)
            if not accepted:
                self.stop("ToolManager rejected the beam-current measurement request.")
                return
        except Exception as ex:
            self.stop("Could not start beam-current measurement: {0}".format(ex))
            return
        finally:
            self.starting_request = False
        buffered = self.early_events
        self.early_events = []
        for event in buffered:
            if self.running and self.pending_operation == "current":
                self._on_beam_current_measurement(event)

    def _on_beam_current_measurement(self, e):
        # Verified delegate: Void Invoke(BeamCurrentMeasurementEventArgs).
        if not self.running or self.pending_operation != "current":
            return
        if self.starting_request:
            self.early_events.append(e)
            return
        expected_names = (DRIVE_POSITION_NAMES["faraday_cup"], EXPECTED_POSITION_NAMES["faraday_cup"])
        if str(e.Position) not in expected_names or bool(e.DriveBack):
            self.stop("Unexpected current-measurement position/return mode: '{0}', driveBack={1}.".format(
                e.Position, e.DriveBack))
            return
        self.current_status = str(e.Status)
        if e.Status == BeamCurrentMeasurementStatus.DrivingStage:
            self.position_name = EXPECTED_POSITION_NAMES["faraday_cup"]
            self.position_state = "Driving"
        elif e.Status in (BeamCurrentMeasurementStatus.Measure, BeamCurrentMeasurementStatus.NewValue,
                          BeamCurrentMeasurementStatus.Finished):
            self.position_name = EXPECTED_POSITION_NAMES["faraday_cup"]
            self.position_state = "Reached (measurement controller)"
        self._report("Beam-current status: {0}; {1}".format(e.Status, e.Comment))
        if e.Status == BeamCurrentMeasurementStatus.Failed:
            self.stop("Beam-current measurement failed: {0}".format(e.Comment))
            return
        if e.Status == BeamCurrentMeasurementStatus.Unknown:
            self.stop("Beam-current measurement reported Unknown status.")
            return
        try:
            if e.Status == BeamCurrentMeasurementStatus.NewValue:
                self.beam_current_in_a = float(e.BeamCurrentInA)
                self._notify_view()
        except Exception as ex:
            self.stop("Could not read beam-current result: {0}".format(ex))
            return
        if e.Status == BeamCurrentMeasurementStatus.Finished:
            if self.beam_current_in_a is None:
                self.stop("Measurement finished without a NewValue event. A fresh current reading could not be verified.")
                return
            if math.isnan(self.beam_current_in_a) or math.isinf(self.beam_current_in_a):
                self.stop("Beam-current measurement returned a non-finite value.")
                return
            # No numerical current acceptance limit has been supplied.
            self._report("Measured beam current: " + format_current_reading(self.beam_current_in_a))
            self._advance()

    def read_actual_working_distance_value(self):
        # Exact live signature and enum verified by runtime inspection.
        # Setter parameter workingDistanceInMm strongly supports mm units for
        # this readback, but getter units and agreement with the autofocus UI
        # still need documentation or comparison before closing the Z loop.
        if self.cc is None:
            raise CalibrationStopped("Column client CC is required for WD readback.")
        return float(self.cc.GetWorkingDistance(
            WorkingDistanceParameterKind.ActualWorkingDistance))

    def _read_beam_mode_identity(self):
        raw_name = self.tm.GetActualSettingsName()
        mode_name = "" if raw_name is None else str(raw_name).strip()
        if not mode_name:
            raise CalibrationStopped("Cannot identify the active beam mode to save.")
        column_name = None
        getter = getattr(self.cc, "GetActualSettingsName", None) if self.cc is not None else None
        if getter is not None:
            raw_column = getter()
            column_name = "" if raw_column is None else str(raw_column).strip()
            if not column_name:
                raise CalibrationStopped("Cannot identify the active column setting to save.")
        return mode_name, column_name

    def save_current_settings(self, step, position_index):
        # User confirmed the isolated check works for Column Mode > beam mode
        # > right-click Save. Save the existing active dataset; no guessed ID,
        # Save As, restore, or manual calibration-valid flag mutation.
        if self.save_target is None:
            raise CalibrationStopped("No beam-mode save target was recorded at sequence start.")
        if self._read_beam_mode_identity() != self.save_target:
            raise CalibrationStopped("Active beam/column mode changed during calibration; refusing to save a different target.")
        self._report("Saving beam mode '{0}'...".format(self.save_target[0]))
        result = self.tm.SaveSettings()
        if not result:
            raise CalibrationStopped("SaveSettings returned False for beam mode '{0}'.".format(self.save_target[0]))
        if self._read_beam_mode_identity() != self.save_target:
            raise CalibrationStopped("Mode changed during Save; the intended target could not be confirmed.")
        self.saved_mode_name = self.save_target[0]
        self._report("Saved beam mode '{0}'.".format(self.saved_mode_name))
        return True

    def _report(self, message):
        self.status_message = str(message)
        if self.status_label is not None:
            self.status_label.Text = str(message)
        try:
            self.ui.Output(str(message) + "\n")
        except Exception:
            print(message)
        self._notify_view()


# Integration notes:
# 1. Confirm and fill POSITION_LIST_INDEXES, then instantiate after the Raith
#    host globals have been initialized:
#       calibration = AutomaticBeamCalibration(TM, UI, adapters)
# 2. Call calibration.start() from the Go button. Saved position-list entries
#    are resolved to the names accepted by ST.DriveStoredPosition(String).
#    The supplied index convention and display-name mapping still need checking.
# 3. Movement, measurement and beam-mode Save are built in. Remaining adapters:
#       expose_three_holes(step, position_list_index)
#       adjust_stage_z_to_working_distance(step, position_list_index)
#    The hole step means perform three exposures; the script does not assume
#    that holes were already burnt.
# 4. For the Z adapter, verify the live CCClient readback method, its return
#    type and units, the stage Z move API and units, movement limits, settling
#    behavior, and the sign/scale of the relationship before enabling motion.
# 5. Movement and measurement use the live reflected signatures. Verify drive
#    names, return-ID semantics and UI thread callback delivery as needed.
#    User confirmed the named current-measurement and beam-mode Save checks.
#    The exposure recipe still needs implementation. No stage or measurement cancellation API has been
#    verified; Stop sequence does not guarantee those operations are canceled.
# 6. The supplied script reports a FOM on ToolManager Idle. Confirm that this
#    event corresponds to completion on the target system, and that GetLast...
#    cannot return a stale result in the installed version.


def _show_sandbox_startup_status():
    """Fallback status if loaded without the Raith host globals."""
    print("Automatic Beam Calibration definitions loaded; no sequence started.")
    missing_indexes = [role for role in ("nanorods", "chessy", "faraday_cup", "sample")
                       if POSITION_LIST_INDEXES.get(role) is None]
    if missing_indexes:
        print("Blocked: confirm and set position-list indexes for: {0}".format(
            ", ".join(missing_indexes)))
    print("Run in the Raith host with TM, ST and UI to open the Start/Stop window.")
    print("Remaining adapters: expose_three_holes,")
    print("  adjust_stage_z_to_working_distance.")


def resolve_reserved_image_slot(member_name):
    """Resolve an exact slot constant demonstrated by the supplied script."""
    reserved = globals().get("ReservedImageSlots")
    if reserved is not None:
        return int(getattr(reserved, member_name))
    # The type's namespace was not supplied. Find the exact documented type and
    # member in loaded Raith assemblies instead of inventing a numeric slot.
    for assembly in AppDomain.CurrentDomain.GetAssemblies():
        try:
            types = assembly.GetTypes()
        except Exception:
            continue
        for candidate in types:
            if candidate.Name == "ReservedImageSlots":
                field = candidate.GetField(member_name)
                if field is not None:
                    return int(field.GetValue(None))
                prop = candidate.GetProperty(member_name)
                if prop is not None:
                    return int(prop.GetValue(None, None))
    return None


def resolve_preview_reference_slot():
    return resolve_reserved_image_slot("AlignmentReferenceSlot")


def configure_emulated_images(pg):
    """Follow the supplied example only for an emulated pattern generator.

    This configures simulated image data; it never starts an acquisition.
    The enum types are obtained from the installed method signatures.
    """
    if pg is None or not pg.IsInEmulationMode():
        return False

    def enum_value(method_name, member):
        for method in pg.GetType().GetMethods():
            parameters = method.GetParameters()
            if method.Name == method_name and len(parameters) == 1 and parameters[0].ParameterType.IsEnum:
                return ClrEnum.Parse(parameters[0].ParameterType, member)
        raise ValueError("Could not resolve enum argument for " + method_name)

    mode = enum_value("EnableLoopback", "SimulatedImageWithNoise")
    pattern = enum_value("SetLoopbackPattern", "Feature")
    pg.EnableLoopback(mode)
    pg.SetLoopbackPattern(pattern)
    return True


class CalibrationDashboard(object):
    """Progress and telemetry only; this class never requests a scan."""

    def __init__(self, controller, ui, image_processor=None, preview_slot=None, overview_slot=None):
        self.c = controller
        self.ui = ui
        self.ip = image_processor
        self.steps = controller._build_steps()
        self.phase_widgets = []
        self.rows = []
        self.image = None
        self.overview_image = None
        self.reference_image = None
        self.preview_step = None
        self.preview_attempt = None
        self.preview_supported = True
        self.has_received_image = False
        self.last_logged_image = None
        offset = 0
        for column, (role, title, labels) in enumerate(PHASE_LAYOUT):
            box = self._box("{0}. {1}".format(column + 1, title),
                            12 + column * 230, 12, 218, 186)
            phase_state = self._label("QUEUED", 12, 22, 194, 18, box)
            phase_count = self._label("0/{0} completed".format(len(labels)), 12, 42, 194, 18, box)
            indexes = list(range(offset, offset + len(labels)))
            self.phase_widgets.append((box, phase_state, phase_count, indexes))
            row_top = 62 if len(labels) > 6 else 66
            row_spacing = min(19, (186 - row_top - 4) // len(labels))
            for row, label in enumerate(labels):
                self.rows.append(self._label("[ ] " + label, 12, row_top + row * row_spacing,
                                             194, row_spacing, box))
            offset += len(labels)
        self.progress = ui.ProgressBar(908)
        self.progress.Left = 12
        self.progress.Top = 210
        self.progress.Height = 10
        self.progress_text = self._label("0/{0} steps completed".format(len(self.steps)), 12, 225, 908, 20)
        self.status = self._label(controller.status_message, 12, 250, 908, 42)
        controller.status_label = self.status

        position_box = self._box("Beam position", 12, 300, 260, 64)
        self.position = self._label("Not requested", 12, 24, 236, 34, position_box)
        current_box = self._box("Beam current", 12, 374, 260, 76)
        self.current = self._label("Not measured", 12, 24, 236, 46, current_box)
        focus_box = self._box("Focus", 12, 488, 572, 94)
        self.focus = self._label("Parabolic autofocus; operator confirmation pending.",
                                 12, 24, 548, 24, focus_box)
        self.confirm_button = ui.Button("Confirm focus and continue")
        self.confirm_button.ChangeContainer(focus_box)
        self.confirm_button.Left, self.confirm_button.Top = 12, 51
        self.confirm_button.Width = 290
        self.confirm_button.Enabled = False

        result_box = self._box("Position alignment results", 284, 300, 300, 178)
        self.fom_labels = {}
        for row, index in enumerate(i for i, step in enumerate(self.steps)
                                    if step.get("threshold") is not None):
            self.fom_labels[index] = self._label("", 12, 22 + row * 38, 276, 38, result_box)

        # ImageBoxIP is a special remote-panel component. The supplied example
        # leaves its image rectangles uncovered by native GroupBoxes. Follow
        # that layout: a heading/status outside the images, with no enclosing
        # native control that can paint an opaque background over the frames.
        self.preview_region = (596, 300, 324, 282)
        self.preview_title = self._label("Current image", 596, 300, 324, 20)
        self.preview_source = self._label("Waiting for image", 608, 554, 300, 22)
        # Remote ScriptPanel controls must be registered before ShowModal. Use
        # only the ImageBoxIP operations present in the user's original script:
        # creation, Left/Top, and SlotOrChannelNumber. No runtime ChangeContainer
        # or Visible command is sent for this special image control.
        if self.ip is not None:
            try:
                if preview_slot is None:
                    preview_slot = resolve_preview_reference_slot()
                if overview_slot is None:
                    overview_slot = resolve_reserved_image_slot("AlignmentOverviewSlot")
                if preview_slot is not None:
                    if overview_slot is not None:
                        self.overview_image = ui.ImageBoxIP(64, 64, False, overview_slot)
                        self.overview_image.Left, self.overview_image.Top = 844, 323
                        self._label("Overview", 844, 394, 64, 20)
                        # Same five-argument constructor form as the example;
                        # its fifth argument's semantic role is not yet verified.
                        self.image = ui.ImageBoxIP(224, 224, False, preview_slot, self.overview_image)
                        self.reference_image = ui.ImageBoxIP(64, 64, False, preview_slot)
                        self.reference_image.Left, self.reference_image.Top = 844, 423
                        self._label("Reference", 844, 494, 64, 20)
                    else:
                        self.image = ui.ImageBoxIP(224, 224, False, preview_slot)
                    self.image.Left, self.image.Top = 608, 323
                    ui.Output("Image viewers initialized on the main panel: reference slot {0}, overview slot {1}.\n".format(preview_slot, overview_slot))
                    self._log_image_api_signature()
                    if self.ip.ImageExists(preview_slot):
                        self.has_received_image = True
                        ui.Output("Cached reference image exists in IP at slot {0}; no calibration action started.\n".format(preview_slot))
                else:
                    self.preview_supported = False
                    ui.Output("Image viewer: ReservedImageSlots.AlignmentReferenceSlot could not be resolved. Supply calibration_preview_slot if needed.\n")
            except Exception as ex:
                self.preview_supported = False
                ui.Output("Image viewer initialization failed: {0}\n".format(ex))
        self.start_button = ui.Button("Start sequence")
        self.start_button.Left = 12
        self.start_button.Top = 600
        self.start_button.Width = 170
        self.stop_button = ui.Button("Stop sequence")
        self.stop_button.Left = 202
        self.stop_button.Top = 600
        self.stop_button.Width = 170
        self.legend = self._label("[OK] completed    > current    [ ] pending    [?] needs setup",
                                  400, 603, 520, 22)
        controller.view = self
        self.refresh()

    def _box(self, title, x, y, width, height):
        box = self.ui.GroupBox(title)
        box.Left, box.Top, box.Width, box.Height = x, y, width, height
        return box

    def _label(self, text, x, y, width, height, parent=None):
        label = self.ui.Label(text)
        if parent is not None:
            label.ChangeContainer(parent)
        label.Left, label.Top, label.Width, label.Height = x, y, width, height
        label.TextColor = Color.DimGray
        return label

    def _needs_setup(self, step):
        return (step.get("kind") == "custom" and
                step.get("adapter") not in ("move_to_position_list_index", "measure_faraday_cup",
                                            "operator_confirm_focus") and
                step.get("adapter") not in self.c.adapters)

    def _log_image_api_signature(self):
        # Read metadata once. This resolves the actual helper parameter names
        # without guessing the meaning of optional ImageBoxIP arguments.
        try:
            for method in self.ui.GetType().GetMethods():
                if method.Name == "ImageBoxIP":
                    parameters = ", ".join("{0}: {1}".format(p.Name, p.ParameterType)
                                           for p in method.GetParameters())
                    self.ui.Output("ImageBoxIP signature: {0}; parameters: {1}\n".format(method, parameters))
        except Exception:
            pass

    def refresh(self):
        c = self.c
        completed = sum(1 for value in c.step_states.values() if value == "done")
        self.progress.Value = int(100.0 * completed / len(self.steps))
        text = "{0}/{1} steps completed".format(completed, len(self.steps))
        if c.running:
            text += "  |  Step {0}: {1}".format(c.step_index + 1, self.steps[c.step_index]["short_name"])
            if c.waiting_for_alignment:
                text += "  |  Attempt {0}/{1}".format(c.attempt, MAX_ATTEMPTS)
                if c.subprogress:
                    text += "  |  Alignment {0}%".format(c.subprogress)
        self.progress_text.Text = text
        self.status.Text = c.status_message
        self.status.TextColor = Color.Firebrick if c.sequence_state == "stopped" else Color.Black
        self.start_button.Enabled = not c.running
        self.stop_button.Enabled = c.running
        self.confirm_button.Enabled = (c.running and c.pending_operation == "confirm")
        for index, row in enumerate(self.rows):
            state = c.step_states.get(index, "pending")
            prefix, color = "[ ] ", Color.DimGray
            if state == "done":
                prefix, color = "[OK] ", Color.ForestGreen
            elif state == "active":
                prefix, color = "> ", Color.RoyalBlue
            elif state == "confirm":
                prefix, color = "[WAIT] ", Color.DarkOrange
            elif state == "stopped":
                prefix, color = "[STOP] ", Color.Firebrick
            elif state == "blocked" or self._needs_setup(self.steps[index]):
                prefix, color = "[?] ", Color.DarkOrange
            row.Text = prefix + self.steps[index]["short_name"]
            row.TextColor = color
        for box, state_label, count_label, indexes in self.phase_widgets:
            done = sum(1 for index in indexes if c.step_states.get(index) == "done")
            count_label.Text = "{0}/{1} completed".format(done, len(indexes))
            title, color = "QUEUED", Color.DimGray
            if done == len(indexes):
                title, color = "COMPLETE", Color.ForestGreen
            elif c.step_index in indexes and c.sequence_state in ("running", "stopped"):
                state = c.step_states.get(c.step_index)
                if state == "blocked":
                    title, color = "NEEDS SETUP", Color.DarkOrange
                elif c.sequence_state == "stopped":
                    title, color = "STOPPED", Color.Firebrick
                elif state == "confirm":
                    title, color = "CONFIRM FOCUS", Color.DarkOrange
                else:
                    title, color = "CURRENT", Color.RoyalBlue
            state_label.Text, state_label.TextColor = title, color
            box.TextColor = color

        if c.position_state in ("Reached (stage controller)", "Reached (measurement controller)"):
            self.position.Text, self.position.TextColor = c.position_name, Color.ForestGreen
        elif c.position_state == "Driving":
            self.position.Text = "Moving to " + c.position_name
            self.position.TextColor = Color.Firebrick
        elif c.position_state == "Move failed":
            self.position.Text = "Move failed: " + c.position_name
            self.position.TextColor = Color.Firebrick
        else:
            self.position.Text, self.position.TextColor = "Not reached yet", Color.DimGray
        if c.beam_current_in_a is None:
            self.current.Text = "No measurement yet" if c.current_status == "Not measured" else c.current_status
        else:
            self.current.Text = "%.9f nA\n%s" % (c.beam_current_in_a * 1e9, c.current_status)
        confirmed = "CONFIRMED by operator" if c.focus_confirmed else "Awaiting operator confirmation"
        self.focus.Text = "Parabolic autofocus | {0}".format(confirmed)
        for index, label in self.fom_labels.items():
            step = self.steps[index]
            site = "Sample" if step["phase"] == "sample" else "Chessy"
            result = c.position_results.get(index)
            caption = "{0}: {1}".format(site, step["short_name"])
            if result is None:
                label.Text = caption + "\nPending  |  required < {0:g} nm".format(step["threshold"])
                label.TextColor = Color.DimGray
            else:
                outcome = "OK" if result["passed"] else "OUTSIDE"
                label.Text = caption + "\n{0:.2f} nm / <{1:g} nm  |  {2}  |  try {3}".format(
                    result["value"], result["limit"], outcome, result["attempt"])
                label.TextColor = Color.ForestGreen if result["passed"] else Color.Firebrick
        self._refresh_preview_visibility()

    def show_existing_procedure_image(self, slot, step_index):
        # ImageExists and ImageBoxIP access existing IP data only. No call to
        # scan, imaging acquisition, patterning or exposure belongs in this view.
        if (not self.preview_supported or self.image is None or self.ip is None or
                not self.ip.ImageExists(slot)):
            return
        try:
            self.image.SlotOrChannelNumber = slot
            self.preview_step = step_index
            self.preview_attempt = self.c.attempt
            self.has_received_image = True
            receipt = (step_index, self.c.attempt, int(slot))
            if receipt != self.last_logged_image:
                self.ui.Output("Image data in IP: step {0}, attempt {1}, slot {2}; viewer selection updated.\n".format(
                    step_index + 1, self.c.attempt, slot))
                self.last_logged_image = receipt
            self._refresh_preview_visibility()
        except Exception:
            self.preview_supported = False
            self.preview_source.Text = "Image unavailable"
            self.preview_source.TextColor = Color.Firebrick

    def reset_image_receipt(self):
        # Retain the prebuilt viewer but never label a frame from an earlier
        # run as the newly received result of a repeated step.
        self.preview_step = None
        self.preview_attempt = None
        self.last_logged_image = None

    def _refresh_preview_visibility(self):
        c = self.c
        confirming = (c.step_index < len(self.steps) and
                      self.steps[c.step_index].get("adapter") == "operator_confirm_focus" and
                      self.preview_step == c.step_index - 1)
        current = (self.preview_step == c.step_index and self.preview_attempt == c.attempt)
        show = self.preview_supported and self.image is not None and (current or confirming)
        if show:
            self.preview_source.Text = "Image received"
            self.preview_source.TextColor = Color.ForestGreen
        elif self.preview_supported and self.image is not None:
            self.preview_source.Text = "Previous image" if self.has_received_image else "Waiting for image"
            self.preview_source.TextColor = Color.DarkOrange if self.has_received_image else Color.DimGray
        else:
            self.preview_source.Text = "Image unavailable"
            self.preview_source.TextColor = Color.Firebrick


def dashboard_window_spec(work_area=None):
    """Allow for window chrome and Raith's Exit row inside the desktop area.

    The compact default also fits the remote-desktop viewport in the supplied
    screenshot. On smaller desktops the native panel may need to scroll rather
    than placing the outer window/Exit button off-screen.
    """
    if work_area is None:
        try:
            area = Screen.PrimaryScreen.WorkingArea
            work_area = (int(area.Left), int(area.Top), int(area.Width), int(area.Height))
        except Exception:
            return GUI_WINDOW_WIDTH, GUI_WINDOW_HEIGHT, 10, 10
    left, top, width, height = work_area
    return (min(GUI_WINDOW_WIDTH, max(320, width - 32)),
            min(GUI_WINDOW_HEIGHT, max(240, height - 50)), left + 10, top + 10)


def launch_calibration_window(adapters=None):
    controller = AutomaticBeamCalibration(TM, UI, adapters, ST, globals().get("CC"))
    dashboard = CalibrationDashboard(controller, UI, globals().get("IP"),
                                     globals().get("calibration_preview_slot"),
                                     globals().get("calibration_overview_slot"))
    try:
        if configure_emulated_images(globals().get("PG")):
            dashboard.preview_title.Text = "Current image (emulated)"
            UI.Output("Emulated feature-image loopback enabled; no acquisition requested.\n")
    except Exception as ex:
        UI.Output("Emulated image setup unavailable: {0}\n".format(ex))

    def start_click(sender, e):
        controller.start()

    def stop_click(sender, e):
        controller.stop("Operator stopped the sequence.")

    def confirm_click(sender, e):
        controller.confirm_focus()

    dashboard.start_button.Click += start_click
    dashboard.stop_button.Click += stop_click
    dashboard.confirm_button.Click += confirm_click
    try:
        # Same API form used in the user's original Raith example. Keeping this
        # modal window open also keeps the script's event handlers alive.
        width, height, left, top = dashboard_window_spec()
        UI.ShowModal(width, height, True, left, top, True, "Automatic Beam Calibration")
    finally:
        if controller.running:
            controller.stop("Calibration window closed.")
        controller._detach_events()
        controller.timer.Dispose()
    return controller


if all(name in globals() for name in ("TM", "ST", "UI")):
    calibration = launch_calibration_window(globals().get("calibration_adapters"))
else:
    _show_sandbox_startup_status()
