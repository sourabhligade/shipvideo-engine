from __future__ import annotations

import json
from typing import Any, Dict


def log_event(event: str, payload: Dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)
