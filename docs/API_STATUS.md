# API evidence and unfinished integration

Snapshot: 13 November 2026. This document describes the published code. Presence of a method in runtime reflection establishes its signature; it does not establish units, instrument behavior or physical correctness. User reports are identified separately from direct test results.

## Evidence by operation

| Operation | Evidence | Remaining uncertainty |
| --- | --- | --- |
| `ST.GetStoredPositionsDisplayNameList()` | User-supplied runtime output enumerated the configured labels at zero-based indexes 3/2/4/5 for Nanorods/cup/Chessy/sample. | The list can change. Names/indexes must be checked on the target installation. |
| `Int32 ST.DriveStoredPosition(String name)` | Runtime signature verified. A supplied screenshot showed the Nanorods move completed. | Code interprets the returned integer as the sequence ID and accepts display labels as drive names. The observed Nanorods path supports this, but does not validate every destination or version. |
| `ST.OnSequenceCompleted` | Runtime event arguments include `Id`, `Success`, and axis errors. Code requires a matching ID and success. | Correlation with the drive return and actual destination semantics need installation-level verification. No image-based location check is performed. |
| `TM.StartAutomaticAlignment(AlignmentProcedure, String)` and `TM.GetLastAlignmentResult()` | Reflected signatures and the supplied working example. Code checks start acceptance and matching `result.Procedure`. | Completion is inferred from `OnComponentStatusChanged` reporting `ComponentStatus.Idle`; matching procedure alone cannot prove result freshness. Concurrent external alignment is not coordinated. |
| `result.DistortionFomInNM` | The supplied alignment script reports this property; the user confirmed this is the displayed FOM to check. | Metric generation belongs to the instrument. The code does not independently recalculate it. |
| `ParabolicAutoFocus` and other option strings | Taken from the supplied alignment example. | Exact support and suitability of all options for every selected procedure still need confirmation. `DistortionFOM=7.0` and `PositionFOM=5.0` are passed options, not the external acceptance limits. |
| `TM.BeamCurrentStartMeasureAtPos(String, Boolean)` | Runtime signature verified. User ran the exact named-cup call with false and reported accepted start, fresh value and completion. | Physical meter accuracy and whether the entire instrument was emulated were not independently established. No acceptable numeric range was supplied. |
| `TM.OnBeamCurrentMeasurement` | Runtime delegate takes one event argument. The reported trace was Started -> DrivingStage -> TurnBeam -> Measure -> NewValue -> TurnBeam -> Finished. | Intermediate events can contain old current values. Code uses NewValue and waits for Finished; no hardware cancellation is implemented. |
| `TM.BeamCurrentGetLastMeasureInA()` | Available by reflection; the user's isolated run agreed with the fresh event and Finished value. | The getter alone cannot establish freshness. The main sequence does not use it as a substitute for NewValue. |
| `Boolean TM.SaveSettings()` | Runtime signature verified; user identified Column Mode > beam mode > right-click Save and reported the isolated check working. Integrated with before/after mode-name checks. | No raw Save trace was supplied. Exact fields persisted, disk durability and behavior after reload were not independently established. |
| `CC.GetWorkingDistance(WorkingDistanceParameterKind.ActualWorkingDistance)` | Exact getter signature and enum member verified by runtime inspection. Setter parameter name `workingDistanceInMm` supports an mm interpretation. | Actual getter units, correspondence to autofocus/UI WD, update timing and freshness still need verification. It is not used to drive Z in this release. |
| `IP.ImageExists`, `UI.ImageBoxIP`, `TM.OnAutomationUpdate.ImageSlot` | Based on supplied image example; user later reported the image display working. | Fifth ImageBoxIP argument's precise semantic role remains unverified. This does not establish all-version rendering behavior or autofocus quality. |
| `PG.IsInEmulationMode`, simulated loopback configuration | Follows the supplied example only when PG reports emulation; enums are resolved through installed metadata. | Image emulation says nothing conclusive about stage/current/Save emulation. |
| Host initialization | Existing host objects were present in supplied runtime inspection. | Exact product/version, assembly versions/paths, supported IronPython version and standalone client initialization are not recorded. |

The user's isolated current result was approximately `4.2e-11 A` (about 42 pA). The finer displayed value was `0.041971975 nA`. These are observations of that run, not an expected current specification. Events before NewValue carried an older `1.9e-9 A` reading.

## Missing three-hole exposure adapter

The planned call is an adapter named `expose_three_holes`. It is absent by default; reaching it stops the sequence before requesting any hole exposure. Supply:

- Hole definition: geometry, size, spacing/orientation and location relative to the saved sample point.
- Intended exposure dose/dwell, beam conditions, patterning area and any recipe dependencies.
- Exact supported API/recipe invocation and its acceptance, completion, failure and cancellation behavior.
- Confirmation of where the beam should be left for the following sample focus/position steps.

Method names exposed on PG are insufficient to infer those settings. No substitute using pre-existing holes has been implemented.

## Missing stage-Z adapter

The planned adapter is `adjust_stage_z_to_working_distance`. It is absent by default. Its configured target is 20.000 +/- 0.005 mm. Before implementation, establish:

1. Compare the actual-WD getter with the native autofocus WD display; verify units, actual versus target channel, and when values become fresh after autofocus.
2. Verify the exact stage-Z read/move API, coordinate convention, units, movement completion and rejection behavior.
3. Obtain site-approved Z bounds, allowed increments, settling requirements and stop behavior.
4. Measure the local sign and scale of Z versus WD. The user's qualitative inverse relationship does not establish an executable gain or safe increment.
5. Define convergence attempts, invalid/stale-value handling and non-convergence behavior, then implement and validate the loop using measured information.

Changing column working distance through `CC.SetWorkingDistance` is not a substitute for the requested physical stage-Z adjustment. No guessed Z gain or motion limits are included.

## Adapter contract and extension limits

The host may define `calibration_adapters` before executing the main file. It is copied into the controller. Keys map to callbacks with this synchronous interface:

```text
callback(step, position_list_index) -> exactly True on completed success
```

The step dictionary carries the adapter name and step-specific options. The hole step carries the sample index. The Z step carries `target_mm`, `tolerance_mm` and `max_attempts`; its second argument is currently `None`, so it must not assume it receives a saved sample index. The existing save adapter uses the same interface.

Missing callbacks, exceptions, or any return value other than exactly True stop advancement. Returning True immediately after submitting an asynchronous operation would incorrectly mark it complete. No asynchronous adapter-completion interface is supplied, and a blocking callback can freeze event/UI delivery. Exposure/Z integration must resolve this design point rather than wrap an unverified asynchronous call in a success callback.

## Other limits requiring attention

- Stop calls `TM.StopAutomaticAlignment()` only while waiting for alignment; exceptions are suppressed. There is no confirmed stage/current cancellation API in use.
- The five-minute timer stops sequence advancement, not necessarily the underlying instrument action. It cannot interrupt synchronous Save/custom callbacks.
- The sample position is operator-entered. No image-based sample identification, height clearance or collision check is implemented here.
- A successful Save happens before sample movement. Later failure does not revert it; a mode change during Save may be detected only after a write has occurred.
- No persistent run log, resume/checkpoint, automatic beam-mode switching, exposure queue, CJob file generation or native exposure-Positionlist execution has been added.
- Original legacy CHM `App`, `Stage` and `Column` calls belong to a different object model and are not interchangeable with the observed `TM`, `ST`, `CC` clients.

## Verification performed for this snapshot

The existing 77 offline tests passed on Python 3.13 using fake clients, events, timers and GUI controls. They cover matching/mismatched/failed completion, early callbacks, rejected starts, FOM retries, current freshness, Save identity checks, watchdog behavior, post-stigmator focus ordering and dashboard state/layout. They do not establish hardware or proprietary GUI behavior.

There is no recorded complete end-to-end live validation of this exact snapshot. The newly added refocus ordering is covered offline; do not infer it was exercised on the instrument from earlier screenshots. Vendor documentation and raw runtime outputs are not redistributed in this repository.
