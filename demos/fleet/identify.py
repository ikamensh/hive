"""Demo: identify the machine you are on — `hive.fleet` standalone.

Task: enroll-time question every fleet tool has to answer. Detect what kind of
host this is (OS, arch, hardware type, availability class) and derive the
stable id a machine with this name gets, without touching any hive service.

    uv run python demos/fleet/identify.py [name]

Works offline; the optional argument shows that a machine's identity follows
its chosen name, not the process that computed it.
"""

import socket
import sys

from hive.fleet import machine_metadata, stable_machine_id

name = sys.argv[1] if len(sys.argv) > 1 else socket.gethostname()
meta = machine_metadata()

print(f"machine name : {name}")
print(f"stable id    : {stable_machine_id(name)}")
print(f"  (in scope 'acme-corp': {stable_machine_id(name, 'acme-corp')})")
for key, value in meta.items():
    print(f"{key:<13}: {value}")
print()
print("Re-run with the same name from any process on any day: same stable id.")
