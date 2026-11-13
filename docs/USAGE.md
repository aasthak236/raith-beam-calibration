# Setup and operating instructions

These instructions describe the code as supplied on 13 November 2026. Use [API status](API_STATUS.md) to distinguish observed behavior from assumptions. The default script cannot finish the sample phase because two instrument adapters are missing.

## 1. Obtain the scripts

Download the repository or copy its Python files to a location accessible to the Raith scripting host. The main file is self-contained and does not import the diagnostic scripts. Keep the documentation and tests available for review.

The exact installed application's script-open/run menu names and the required script folder have not been supplied. Use the same Raith IronPython scripting environment used for the existing alignment examples; these instructions do not invent menu paths.

## 2. Check the host environment

The host must already provide its initialized clients. The main file imports `System`, `System.Drawing`, `System.Windows.Forms` and the following from `Raith.CommonLib`:

```python
AlignmentProcedure
ComponentStatus
WorkingDistanceParameterKind
BeamCurrentMeasurementStatus
```

| Object | Use |
| --- | --- |
| `TM` | Automatic alignment, completion/status events, current measurement, beam-mode identity and Save. |
| `ST` | Saved-position names, movement and sequence completion. |
| `UI` | Dashboard controls, modal window and text output. |
| `AD` | `ReadSysBool("RSServer.Enabled", False)` during construction of alignment options. |
| `CC` | Optional active column-setting identity check; required for the standalone WD getter and any future Z integration using it. |
| `IP` | Optional existing image-slot data for the dashboard. |
| `PG` | Optional check/configuration of simulated image loopback. |

The file's automatic launch guard checks only `TM`, `ST` and `UI`; this is not a complete dependency check. In particular, `AD` is needed when the configured standard-settings path is evaluated. Missing .NET/Raith imports can fail before the launch guard runs.

No client-construction or connection-initialization recipe is included. Exact assembly locations, software versions and non-host launch instructions are unknown.

## 3. Verify the saved positions

Run `InspectRaithDetails.py` in the host to read API metadata and enumerate saved-position display labels without submitting movement, exposure, measurement or Save.

Compare the output with this configuration near the top of `AutomaticBeamCalibration.py`:

| Role | Zero-based index | Exact expected display label |
| --- | ---: | --- |
| Nanorods | 3 | `Nanorods` |
| Faraday Cup | 2 | `Faraday Cup on USH` |
| Chessy | 4 | `Chessy` |
| Sample | 5 | `sample` |

The user supplied these numbers and the runtime enumeration matched them. Some original inline TODO comments remain as a reminder to check the installation's current list. Do not subtract one automatically.

`POSITION_LIST_INDEXES` selects the entry to validate; `EXPECTED_POSITION_NAMES` defines its required label; `DRIVE_POSITION_NAMES` defines the string passed to `ST.DriveStoredPosition`. They currently use identical names. Change these only to values established on the target installation. The script checks the labels at Start and again before each move.

The sample entry is supplied by the operator. The code does not locate the sample by imaging, detect sample edges, or independently verify the physical surface. A green destination means controller-reported arrival, not image-based sample identification.

## 4. Select the beam mode and review the configuration

Select the intended beam mode using the normal instrument interface before starting. The script reads and records the active TM mode name and, where available, CC setting name. It does not select a beam mode for you. Keep these unchanged through the run; Save checks their identity before and after writing.

Review these current values in the main script:

| Setting | Supplied value | Meaning |
| --- | --- | --- |
| `MAX_ATTEMPTS` | `3` | Maximum attempts for position FOM acceptance. |
| `OPERATION_TIMEOUT_MS` | `300000` | Five-minute watchdog for movement, alignment and measurement waits; chosen configuration, not a hardware specification. |
| `TARGET_WORKING_DISTANCE_MM` | `20.000` | Target for the missing Z adapter. |
| `WORKING_DISTANCE_TOLERANCE_MM` | `0.005` | Tolerance for the missing Z adapter. |
| `DISTORTION_FOM_SETTING` / `POSITION_FOM_SETTING` | `7.0` / `5.0` | Options passed to the alignment routine; separate from the controller's acceptance thresholds. |

`_alignment_parameters` also adds `ImprovedFocusMap`, `PresetFieldRotation` when enabled, `EnableLogging` and `ParabolicAutoFocus`. Standard settings are conditional on `AD.ReadSysBool`. Full-field stigmation adds `NumberOfCoefficients=6`. These came from the supplied example; applicability to every procedure still needs confirmation. No numerical beam-current acceptance range is configured.

## 5. Open the main dashboard

Execute `AutomaticBeamCalibration.py` in the host. With `TM`, `ST` and `UI` present, it opens **Automatic Beam Calibration**. The nominal window is 960 by 700 logical pixels and is reduced to the available desktop area. Very small displays may require scrolling.

Opening creates the GUI and may display a cached image. If `PG.IsInEmulationMode()` reports true, the code attempts the demonstrated simulated image-loopback configuration. Opening does not start movement, alignment, measurement or Save.

Image emulation is not a global dry-run mode. This code does not prove that stage motion, current measurement or saving is emulated. Establish the host's actual operating mode using the site's normal procedure before submitting actions.

## 6. Start and inspect the sequence

Press **Start sequence**. Setup validates the stored-position labels, records the Save target and subscribes to required completion events. A setup failure leaves the sequence stopped with an explanation.

The script performs these steps in order:

1. Move to Nanorods.
2. Beam focus (`DynamicFocus`).
3. Stigmator (`DynamicStigmator`).
4. Beam refocus (`DynamicFocus`).
5. Wait for the operator to inspect focus and press **Confirm focus and continue**. Use Stop if focus is unsatisfactory. No focus FOM is checked.
6. Field focus (`DynamicFocusFullField`).
7. Field stigmator (`DynamicStigmatorFullField`).
8. Field refocus (`DynamicFocusFullField`).
9. Move to Chessy.
10. Beam focus.
11. Stigmator.
12. Beam refocus.
13. Position coarse (`PositionCoarse`), reported FOM strictly below 100 nm.
14. Position fine (`PositionFine`), reported FOM strictly below 50 nm.
15. Position final (`DistortionUsingStage`), reported FOM strictly below 10 nm.
16. Move to Faraday Cup on USH.
17. Measure using `TM.BeamCurrentStartMeasureAtPos("Faraday Cup on USH", False)`. The named operation may position/check the cup again. Wait for a fresh `NewValue` followed by `Finished`; the script does not request drive-back.
18. Save the active beam mode using parameterless `TM.SaveSettings()`, requiring a true return and unchanged mode identity.
19. Move to sample.
20. Expose three holes: **missing adapter; default run stops here**.
21. Sample beam focus: planned, reached only after a real hole-exposure adapter reports success.
22. Adjust stage Z to 20.000 +/- 0.005 mm: **missing adapter**.
23. Sample position final, reported FOM strictly below 10 nm: reached only after both missing operations are implemented and succeed.

The holes are intended to be newly exposed. Existing holes are not assumed and the missing step is not silently skipped. Do not provide a dummy success callback to make the run appear complete.

## 7. Read the dashboard

- Four cards show completed, current, waiting and missing-setup steps. The status description below the tracker gives the current action or stopping reason.
- Beam position shows the named destination, red while driving and green after controller-reported arrival.
- Position results show reported FOM, limit and attempt. The value is `result.DistortionFomInNM`; equality to the limit fails the strict check. Failure to meet it after three attempts stops advancement.
- Beam current is displayed in scientific amperes and decimal nanoamperes. A finite fresh reading confirms receipt, not compliance with an unspecified current target.
- The image pane follows existing image slots from focus, stigmator and alignment updates. It does not request a new scan. `Previous image` means cached or from an earlier step/attempt/run; `Image received` means data was received for the tracked operation. Use the native instrument view if the optional pane is unavailable or insufficient for inspection.

There is one operator focus confirmation at Nanorods. Later focus steps do not pause for an additional confirmation.

## 8. Stop, exit and restart

**Stop sequence**, closing the window, a watchdog expiry, a failed result or a missing adapter prevents further advancement. During an alignment wait the code attempts `TM.StopAutomaticAlignment()`. It has no verified cancellation path for stage motion or current measurement; use the corresponding native instrument stop if required. A completed Save is not undone.

The watchdog does not cover the operator-confirmation pause and cannot interrupt a synchronous custom adapter or blocked Save call. The supplied custom-adapter contract is synchronous.

Starting again rebuilds the sequence from step 1. There is no checkpoint/resume or rollback. Check the stopped instrument state before starting a fresh run. The script sends status lines to `UI.Output`; it does not create a persistent run-log file. Capture relevant output manually when reporting a problem.

## 9. Optional isolated diagnostics

Close the main calibration window before running an isolated check, to avoid overlapping actions/event subscriptions.

### Beam current

Run `BeamCurrentMeasurementCheck.py`. Opening only constructs its window. Press **Measure at Faraday cup** to request movement/measurement at the named cup with drive-back false. Compare `REQUEST`, `EVENT` and `RESULT` output and the normal instrument current display. The diagnostic uses a two-minute watchdog. **Stop waiting** detaches the diagnostic; it is not a hardware cancel.

### Beam-mode Save

Run `SaveBeamModeCheck.py`. Opening reads the current mode and available state. Compare the displayed names with the intended Column Mode beam mode. **Read active mode** refreshes the target; **Save active beam mode** invokes the parameterless Save. Review `SELECTION`, `BEFORE SAVE`, `SAVE REQUEST`, `SAVE RETURN` and `AFTER SAVE`, and the native modified/Save indicator. The check does not restore or reload a mode to prove persistence.

### API inspection

`InspectRaithRuntime.py` prints matching .NET metadata. `InspectRaithDetails.py` additionally prints parameter/enum/event details and reads saved-position labels. Neither requests movement, focus, exposure, measurement, initialization or Save. Review output before sharing it publicly; raw outputs are not included in this package.

## 10. Troubleshooting

| Symptom | What to check |
| --- | --- |
| `No module named System` or missing `Raith.CommonLib` | The file requires the Raith .NET scripting host and its assemblies; ordinary Python is not the launch route. |
| Definitions load but no dashboard | Check the presence of host globals `TM`, `ST`, `UI`; imports must also have succeeded. |
| Label/index mismatch | Read the current stage saved-position list; compare exact spelling, case and index. Do not replace it with spreadsheet Positionlist row numbers. |
| Standard-settings parameter construction fails | Check `AD` and `ReadSysBool`, and the installed alignment option support. |
| Waiting for a move despite an event | The returned integer is treated as the expected sequence ID. Other IDs are ignored; verify the installed return/event semantics. |
| Blank or previous image | Inspect image receipt output and native instrument image. Image availability and successful rendering are separate concerns. Do not add scans simply to refresh the dashboard. |
| Measurement finishes without fresh data | Earlier event values or the last-measure getter may be stale. The main script requires `NewValue` before `Finished`. |
| Save refused | Inspect active names, mode changes, exceptions and the Boolean return. Do not substitute a guessed dataset ID or Save As. |
| Missing `expose_three_holes` adapter | Expected stopping point in this release; exposure recipe/API are unfinished. |
| Missing `adjust_stage_z_to_working_distance` adapter | Expected until WD and stage-Z behavior are established and implemented. |

## 11. Run offline checks

From the downloaded repository root, run:

```text
python -m unittest discover -s tests -v
```

This uses only the standard library and fake Raith/.NET objects. The documented run passed 77 tests on Python 3.13. It submits no instrument requests. There is no supplied CI workflow or requirement to install the Raith runtime for these tests.
