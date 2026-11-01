from __future__ import annotations

import os
import sys
import time
from pathlib import Path


marker = Path(os.getenv("DATA_DIR", "/data")) / "last_heartbeat"
max_age = max(90, int(os.getenv("HEARTBEAT_SECONDS", "30")) * 3)
if not marker.is_file() or time.time() - marker.stat().st_mtime > max_age:
    sys.exit(1)
