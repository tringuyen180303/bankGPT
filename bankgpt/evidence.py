from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bankgpt.policy.guard import redact_obj

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "evidence"


class EvidenceLog:
    def __init__(self, run_id: str, subdir: str | None = None) -> None:
        folder = EVIDENCE_ROOT / (subdir or run_id)
        folder.mkdir(parents=True, exist_ok=True)
        self.dir = folder
        self.run_id = run_id
        self.path = folder / "run.jsonl"

    def event(self, kind: str, **payload: Any) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "kind": kind,
            **redact_obj(payload),
        }
        with self.path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    def write_json(self, name: str, data: Any) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(redact_obj(data), indent=2) + "\n")
        return path
