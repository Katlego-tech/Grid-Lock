"""US6: the triage chain is portable -- no transport or storage library in its import graph.

triage.md section 9, item 1. Run in a fresh interpreter, so modules other tests imported
cannot hide or fake a leak.
"""

from __future__ import annotations

import json
import subprocess
import sys

FORBIDDEN = ("fastapi", "starlette", "sqlalchemy", "aio_pika", "psycopg")

PROBE = f"""
import json, sys
import gridlock_triage.chain
leaked = sorted(
    name for name in sys.modules
    if name.split(".")[0] in {FORBIDDEN!r}
)
print(json.dumps(leaked))
"""


def test_importing_the_chain_loads_no_transport_or_storage_module() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == []
