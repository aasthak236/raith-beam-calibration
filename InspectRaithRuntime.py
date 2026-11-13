# Run in the same Raith scripting sandbox used for AutomaticBeamCalibration.py.
# Compatible with Python 2 / IronPython-style syntax and Python 3.
# Prints API metadata only. It does not call movement, exposure, initialization,
# measurement, or save methods, and does not read position-list data.

def report(message):
    print(message)


def inspect_object(name, obj, words):
    report("\nOBJECT: " + name)
    try:
        runtime_type = obj.GetType()
        report("TYPE: " + str(runtime_type.FullName))
        types = [runtime_type]
        try:
            types.extend(list(runtime_type.GetInterfaces()))
        except Exception as exc:
            report("Interface metadata unavailable: " + str(exc))
        found = set()
        for reflected_type in types:
            for collection_name in ("GetMethods", "GetProperties", "GetEvents"):
                try:
                    members = getattr(reflected_type, collection_name)()
                    for member in members:
                        if any(word in str(member.Name).lower() for word in words):
                            found.add(str(member.ToString()))
                except Exception as exc:
                    report("Metadata unavailable for " + collection_name + ": " + str(exc))
        for signature in sorted(found):
            report(signature)
        if not found:
            report("No matching .NET metadata found.")
    except Exception as exc:
        report(".NET reflection unavailable for this object: " + str(exc))
        report("Python-visible matching member names:")
        try:
            for member_name in sorted(dir(obj)):
                if any(word in member_name.lower() for word in words):
                    report(member_name)
        except Exception as fallback_error:
            report("Member listing unavailable: " + str(fallback_error))


report("Raith runtime inspection started; metadata only.")
targets = (
    ("ST", ("stored", "position", "drivetemplate", "movecompleted", "sequencecompleted")),
    ("TM", ("alignment", "beamcurrent", "measure", "save", "settings", "expos")),
    ("CC", ("workingdistance", "mainfocus", "beamcurrent", "measurement")),
    ("PG", ("pattern", "expos", "dose", "beamcurrent", "measure")),
    ("AD", ("position", "list")),
    ("App", ("position", "execscan", "exec", "save")),
    ("Stage", ("drive", "position", "wait", "z")),
    ("Column", ("workingdistance", "savewritefield", "current")),
)
available_globals = globals()
for object_name, keywords in targets:
    if object_name in available_globals and available_globals[object_name] is not None:
        inspect_object(object_name, available_globals[object_name], keywords)
    else:
        report("\nOBJECT NOT PRESENT: " + object_name)
report("\nRaith runtime inspection finished.")
