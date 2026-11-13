# Candidate mapping for the user's Column Mode > beam mode > right-click Save.
# TM.SaveSettings() exists and returns Boolean in the installed runtime.
# Its equivalence to that UI command is still being checked. CC.SaveSettings
# has a different documented scope (power-supply INI settings).
# Loading only reads metadata/state and opens a window. Only the Save button
# calls TM.SaveSettings(). No Save As, restore, calibration-valid setter,
# stage movement, exposure or beam-current measurement is performed.

from System.Drawing import Color


class SaveBeamModeCheck(object):
    def __init__(self, tm, ui, cc=None):
        self.tm, self.ui, self.cc = tm, ui, cc
        self.expected = None
        self.snapshot = None
        self.busy = False
        self.saved = False
        self.outcome = "ready"
        self.message = "Read the active mode and compare it with the intended beam mode."
        self.on_change = None

    def _log(self, message):
        try:
            self.ui.Output(str(message) + "\n")
        except Exception:
            print(message)

    def _refresh(self):
        if self.on_change is not None:
            self.on_change()

    def read_snapshot(self):
        mode = str(self.tm.GetActualSettingsName()).strip()
        if not mode:
            raise ValueError("ToolManager returned an empty active-settings name.")
        column = None
        if self.cc is not None:
            column = str(self.cc.GetActualSettingsName()).strip()
        data = {"mode": mode, "column": column}
        # These reads are diagnostic only; they are not proof of persistence.
        for key, getter in (("state", "GetSettingsState"),
                            ("calibration_valid", "ToolCalibrationDataIsValid"),
                            ("current_a", "BeamCurrentGetLastMeasureInA")):
            try:
                data[key] = getattr(self.tm, getter)()
            except Exception as ex:
                data[key] = "Unavailable: " + str(ex)
        return data

    def describe(self, prefix, data):
        self._log("{0}: TM mode='{1}'; CC setting='{2}'; settings state={3}; calibration valid={4}".format(
            prefix, data["mode"], data["column"], data["state"], data["calibration_valid"]))
        try:
            current = float(data["current_a"])
            self._log("{0}: last measured current=%.9e A (%.9f nA)".format(prefix) % (
                current, current * 1e9))
        except Exception:
            self._log(prefix + ": last current unavailable")

    def inspect(self):
        if self.busy:
            return
        self.saved = False
        try:
            data = self.read_snapshot()
            self.expected = (data["mode"], data["column"])
            self.snapshot = data
            self.describe("SELECTION", data)
            self.outcome = "ready"
            self.message = "Selected mode: '{0}'. Check it against Column Mode before Save.".format(data["mode"])
        except Exception as ex:
            self.expected = None
            self.outcome = "failed"
            self.message = "Could not identify the save target: " + str(ex)
            self._log(self.message)
        self._refresh()

    def save(self):
        if self.busy or self.expected is None or self.saved:
            return
        self.busy = True
        self.outcome = "saving"
        self.message = "Saving selected active mode..."
        self._refresh()
        try:
            before = self.read_snapshot()
            if (before["mode"], before["column"]) != self.expected:
                raise ValueError("The active mode changed. Read the selection again before saving.")
            self.describe("BEFORE SAVE", before)
            self._log("SAVE REQUEST: TM.SaveSettings() for active mode '{0}'".format(before["mode"]))
            result = self.tm.SaveSettings()
            self._log("SAVE RETURN: " + str(result))
            after = self.read_snapshot()
            self.snapshot = after
            self.describe("AFTER SAVE", after)
            if (after["mode"], after["column"]) != self.expected:
                raise ValueError("Mode changed during Save. The return value alone cannot verify the intended target.")
            if result is not True and not bool(result):
                raise ValueError("SaveSettings returned False; Save was not confirmed.")
            self.saved = True
            self.outcome = "reported_saved"
            self.message = "SaveSettings reported success for '{0}'. Check the Column Mode Save/modified indicator.".format(after["mode"])
        except Exception as ex:
            self.outcome = "failed"
            self.message = "Save not confirmed: " + str(ex)
            self._log(self.message)
        finally:
            self.busy = False
            self._refresh()


def launch_save_beam_mode_check():
    check = SaveBeamModeCheck(TM, UI, globals().get("CC"))
    title = UI.Label("Beam-mode Save check")
    title.Left, title.Top, title.Width = 15, 15, 650
    target = UI.Label("Reading active mode...")
    target.Left, target.Top, target.Width, target.Height = 15, 45, 650, 60
    status = UI.Label(check.message)
    status.Left, status.Top, status.Width, status.Height = 15, 115, 650, 60
    scope = UI.Label("Save calls TM.SaveSettings() on the active mode. Compare the names with Column Mode first.")
    scope.Left, scope.Top, scope.Width, scope.Height = 15, 185, 650, 40
    read = UI.Button("Read active mode")
    read.Left, read.Top, read.Width = 15, 240, 180
    save = UI.Button("Save active beam mode")
    save.Left, save.Top, save.Width = 220, 240, 240

    def refresh():
        data = check.snapshot
        target.Text = "TM mode: {0}\nColumn setting: {1}".format(
            data["mode"] if data else "Unknown", data["column"] if data else "Unknown")
        status.Text = check.message
        status.TextColor = (Color.ForestGreen if check.outcome == "reported_saved" else
                            Color.Firebrick if check.outcome == "failed" else Color.Black)
        read.Enabled = not check.busy
        save.Enabled = not check.busy and check.expected is not None and not check.saved

    def inspect_click(sender, e):
        check.inspect()

    def save_click(sender, e):
        check.save()

    check.on_change = refresh
    read.Click += inspect_click
    save.Click += save_click
    check.inspect()
    UI.ShowModal(700, 330, True, 10, 10, True, "Beam-mode Save check")
    return check


if all(name in globals() for name in ("TM", "UI")):
    save_beam_mode_check = launch_save_beam_mode_check()
else:
    print("Run in the Raith host with TM and UI. No Save operation performed.")
