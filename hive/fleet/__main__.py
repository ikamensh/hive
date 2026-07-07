"""`python -m hive.fleet` — print this host's identity as JSON."""

import json
import socket

from hive.fleet import machine_metadata, stable_machine_id

name = socket.gethostname()
print(
    json.dumps(
        {"name": name, "machine_id": stable_machine_id(name), **machine_metadata()},
        indent=2,
    )
)
