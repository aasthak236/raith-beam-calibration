# Offline tests

From the repository root, run:

```text
python -m unittest discover -s tests -v
```

The 77 tests use fake Raith clients, .NET objects, events, timers and GUI controls. They submit no instrument requests. A passing run does not establish hardware behavior, proprietary GUI rendering or completion of the unfinished sample operations.

Keep the test files in this directory: the diagnostic tests share the fake runtime defined by `test_calibration_offline.py`.
