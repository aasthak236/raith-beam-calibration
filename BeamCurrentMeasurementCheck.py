# Run in the Raith scripting host with TM, ST and UI.
# Loading opens a window only. The Measure button requests a named-position
# beam-current measurement: it can move the stage and turn the beam as part
# of measurement. No autofocus, image acquisition, exposure recipe or save
# operation is requested here. False means do not drive back afterwards.
# Exact method signatures, argument names, event fields and enum members were
# supplied by reflection from the user's installed Raith runtime.

import math
from System.Windows.Forms import Timer
from System.Drawing import Color
from Raith.CommonLib import BeamCurrentMeasurementStatus

CUP_INDEX = 2
CUP_NAME = "Faraday Cup on USH"
MEASUREMENT_TIMEOUT_MS = 120000  # Diagnostic watchdog, not an instrument specification.


def format_current_reading(value_a):
    value_a = float(value_a)
    return "%.9e A (%.9f nA)" % (value_a, value_a * 1e9)


def format_amp_value(value_a):
    return "not received" if value_a is None else "%.9e" % float(value_a)


class BeamCurrentCheck(object):
    def __init__(self, tm, st, ui):
        self.tm, self.st, self.ui = tm, st, ui
        self.running = False
        self.starting = False
        self.attached = False
        self.buffer = []
        self.fresh_value_a = None
        self.finished_value_a = None
        self.last_value_a = None
        self.freshness_verified = False
        self.message = "Ready. No measurement started."
        self.outcome = "ready"
        self.on_change = None
        self.handler = self._on_measurement
        self.timer = Timer()
        self.timer.Interval = MEASUREMENT_TIMEOUT_MS
        self.timer.Tick += self._on_timeout

    def _log(self, message):
        try:
            self.ui.Output(str(message) + "\n")
        except Exception:
            print(message)

    def _refresh(self):
        if self.on_change is not None:
            self.on_change()

    def start(self):
        if self.running:
            return
        try:
            names = list(self.st.GetStoredPositionsDisplayNameList())
            if CUP_INDEX >= len(names) or str(names[CUP_INDEX]) != CUP_NAME:
                raise ValueError("Position index 2 no longer identifies " + CUP_NAME)
        except Exception as ex:
            self.message = "Could not validate cup: " + str(ex)
            self.outcome = "failed"
            self._log(self.message)
            self._refresh()
            return
        self.fresh_value_a = None
        self.finished_value_a = None
        self.last_value_a = None
        self.freshness_verified = False
        self.buffer = []
        self.running = True
        self.outcome = "running"
        self.message = "Requesting measurement at " + CUP_NAME
        try:
            self.tm.OnBeamCurrentMeasurement += self.handler
            self.attached = True
            self.starting = True
            self.timer.Start()
            self._log("REQUEST: TM.BeamCurrentStartMeasureAtPos('{0}', False)".format(CUP_NAME))
            accepted = self.tm.BeamCurrentStartMeasureAtPos(CUP_NAME, False)
            self._log("Start request accepted: " + str(accepted))
            if not accepted:
                self._finish("ToolManager rejected the measurement request.", "failed")
                return
        except Exception as ex:
            self._finish("Measurement request raised: " + str(ex), "failed")
            return
        finally:
            self.starting = False
        pending = self.buffer
        self.buffer = []
        self._refresh()
        for event in pending:
            if self.running:
                self._on_measurement(event)

    def _on_measurement(self, e):
        # The installed delegate takes ONE argument, not sender plus event.
        if not self.running:
            return
        if self.starting:
            self.buffer.append(e)
            return
        self._log("EVENT: status={0}; valueA={1}; position='{2}'; driveBack={3}; progress={4}; comment={5}".format(
            e.Status, format_amp_value(e.BeamCurrentInA), e.Position, e.DriveBack, e.Progress, e.Comment))
        self.message = "Measurement: " + str(e.Status)
        if e.Status == BeamCurrentMeasurementStatus.Failed:
            self._finish("Measurement failed: " + str(e.Comment), "failed")
            return
        if e.Status == BeamCurrentMeasurementStatus.NewValue:
            try:
                self.fresh_value_a = float(e.BeamCurrentInA)
            except Exception as ex:
                self._log("Could not read NewValue: " + str(ex))
        if e.Status == BeamCurrentMeasurementStatus.Finished:
            try:
                self.finished_value_a = float(e.BeamCurrentInA)
            except Exception as ex:
                self._log("Could not read Finished event value: " + str(ex))
            try:
                self.last_value_a = float(self.tm.BeamCurrentGetLastMeasureInA())
            except Exception as ex:
                self._log("Could not read last-measure getter: " + str(ex))
            self._log("RESULT: NewValue={0} A; Finished={1} A; getter={2} A".format(
                format_amp_value(self.fresh_value_a), format_amp_value(self.finished_value_a),
                format_amp_value(self.last_value_a)))
            value = self.fresh_value_a
            self.freshness_verified = value is not None and not math.isnan(value) and not math.isinf(value)
            if self.freshness_verified:
                self._finish("Finished; fresh reading received: " + format_current_reading(value) + ".", "finished")
            else:
                self._finish("Finished, but no finite NewValue was received. Freshness remains unverified.", "unverified")
            return
        # Unknown is logged, not assumed terminal without method documentation.
        self._refresh()

    def _finish(self, message, outcome):
        self.running = False
        self.timer.Stop()
        if self.attached:
            try:
                self.tm.OnBeamCurrentMeasurement -= self.handler
            except Exception:
                pass
            self.attached = False
        self.message, self.outcome = message, outcome
        self._log(message)
        self._refresh()

    def _on_timeout(self, sender, e):
        self.stop_waiting("No terminal measurement event within {0} seconds.".format(
            MEASUREMENT_TIMEOUT_MS // 1000))

    def stop_waiting(self, reason="Operator stopped waiting."):
        if self.running:
            self._finish(reason + " The instrument operation may still be active; use the instrument's native stop control if needed.", "stopped")


def launch_beam_current_check():
    check = BeamCurrentCheck(TM, ST, UI)
    title = UI.Label("Target: Faraday Cup on USH (saved position 2)")
    title.Left, title.Top, title.Width = 15, 15, 610
    action = UI.Label("Measure drives to the cup and measures current. It does not return to the previous position.")
    action.Left, action.Top, action.Width, action.Height = 15, 43, 610, 40
    status = UI.Label(check.message)
    status.Left, status.Top, status.Width, status.Height = 15, 90, 610, 60
    value = UI.Label("No fresh reading yet")
    value.Left, value.Top, value.Width, value.Height = 15, 155, 610, 55
    scope = UI.Label("A fresh reading confirms receipt only. No acceptable current range has been configured.")
    scope.Left, scope.Top, scope.Width, scope.Height = 15, 215, 610, 40
    measure = UI.Button("Measure at Faraday cup")
    measure.Left, measure.Top, measure.Width = 15, 270, 240
    stop = UI.Button("Stop waiting")
    stop.Left, stop.Top, stop.Width = 280, 270, 170

    def refresh():
        measure.Enabled, stop.Enabled = not check.running, check.running
        status.Text = check.message
        status.TextColor = (Color.ForestGreen if check.outcome == "finished" else
                            Color.Firebrick if check.outcome in ("failed", "stopped") else
                            Color.DarkOrange if check.outcome == "unverified" else Color.Black)
        if check.fresh_value_a is None:
            value.Text = "No fresh reading yet"
        else:
            value.Text = "NewValue: " + format_current_reading(check.fresh_value_a)

    def start_click(sender, e):
        check.start()

    def stop_click(sender, e):
        check.stop_waiting()

    check.on_change = refresh
    measure.Click += start_click
    stop.Click += stop_click
    refresh()
    try:
        UI.ShowModal(660, 355, True, 10, 10, True, "Beam current measurement check")
    finally:
        check.stop_waiting("Measurement window closed.")
        check.timer.Dispose()
    return check


if all(name in globals() for name in ("TM", "ST", "UI")):
    beam_current_check = launch_beam_current_check()
else:
    print("Run in the Raith scripting host with TM, ST and UI. No measurement started.")
