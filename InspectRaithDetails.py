# Run in the same Raith sandbox as the calibration script.
# Read-only: reads stored-position display names and API metadata.
# Does not call drive, autofocus, exposure, measurement, initialization or save.
from System import Enum


def out(value):
    print(value)


seen_enums = set()


def describe_enum(enum_type):
    if enum_type.IsByRef:
        enum_type = enum_type.GetElementType()
    if not enum_type.IsEnum or str(enum_type.FullName) in seen_enums:
        return
    seen_enums.add(str(enum_type.FullName))
    out("  ENUM " + str(enum_type.FullName))
    for name in Enum.GetNames(enum_type):
        out("    {0} = {1}".format(name, enum_type.GetField(name).GetRawConstantValue()))


def describe_members(data_type):
    if data_type.IsByRef:
        data_type = data_type.GetElementType()
    describe_enum(data_type)
    if str(data_type.FullName).startswith("System.") or data_type.IsEnum:
        return
    out("  DATA TYPE " + str(data_type.FullName))
    for prop in data_type.GetProperties():
        out("    PROPERTY " + str(prop.ToString()))
        describe_enum(prop.PropertyType)
    for field in data_type.GetFields():
        out("    FIELD " + str(field.ToString()))
        describe_enum(field.FieldType)


def describe_api(obj, methods, events):
    obj_type = obj.GetType()
    out("TYPE " + str(obj_type.FullName))
    types = [obj_type] + list(obj_type.GetInterfaces())
    seen = set()
    for inspected_type in types:
        for method in inspected_type.GetMethods():
            signature = str(method.ToString())
            if method.Name not in methods or signature in seen:
                continue
            seen.add(signature)
            out("METHOD " + signature)
            for parameter in method.GetParameters():
                out("  PARAM {0}: {1}; optional={2}".format(
                    parameter.Name, parameter.ParameterType, parameter.IsOptional))
                if parameter.IsOptional:
                    out("    DEFAULT " + str(parameter.DefaultValue))
                describe_enum(parameter.ParameterType)
            describe_enum(method.ReturnType)
        for event in inspected_type.GetEvents():
            signature = "EVENT " + str(event.Name)
            if event.Name not in events or signature in seen:
                continue
            seen.add(signature)
            out(signature)
            invoke = event.EventHandlerType.GetMethod("Invoke")
            out("  DELEGATE " + str(invoke.ToString()))
            for parameter in invoke.GetParameters():
                out("  ARG {0}: {1}".format(parameter.Name, parameter.ParameterType))
                describe_members(parameter.ParameterType)


out("Raith detailed inspection started; read-only.")
targets = (
    ("ST", ("DriveStoredPosition", "GetStoredPositionsDisplayNameList",
            "DriveTemplate", "GetAxisPosition", "SetAxisPosition"),
           ("OnSequenceCompleted", "OnMoveCompleted")),
    ("CC", ("GetWorkingDistance", "GetMainFocus", "SetWorkingDistance"),
           ("OnWorkingDistanceChanged",)),
    ("TM", ("BeamCurrentStartMeasure", "BeamCurrentStartMeasureAtPos",
            "BeamCurrentGetLastMeasureInA", "SaveSettings", "StartAutomaticAlignment"),
           ("OnBeamCurrentMeasurement",)),
)
runtime_globals = globals()
for object_name, method_names, event_names in targets:
    out("\nOBJECT " + object_name)
    if object_name not in runtime_globals:
        out("  Object not present")
        continue
    try:
        describe_api(runtime_globals[object_name], method_names, event_names)
    except Exception as exc:
        out("  Metadata inspection failed: " + str(exc))

out("\nSTORED-POSITION DISPLAY NAMES (enumerated from zero; no movement)")
if "ST" in runtime_globals:
    try:
        display_names = list(ST.GetStoredPositionsDisplayNameList())
        for index, name in enumerate(display_names):
            out("  [{0}] {1}".format(index, name))
        out("Count: " + str(len(display_names)))
        out("User-supplied numbers, shown below as direct zero-based lookups only:")
        for role, index in (("nanorods", 3), ("faraday_cup", 2),
                            ("chessy", 4), ("sample", 5)):
            if 0 <= index < len(display_names):
                out("  {0}: index {1} -> {2}".format(role, index, display_names[index]))
            else:
                out("  {0}: index {1} is out of range".format(role, index))
        out("This does not confirm that DriveStoredPosition accepts display labels,")
        out("or that the user's numbers use zero-based indexing. Confirm by name.")
    except Exception as exc:
        out("  Could not read display-name list: " + str(exc))
out("\nRaith detailed inspection finished.")
