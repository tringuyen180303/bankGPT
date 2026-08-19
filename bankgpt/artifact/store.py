from __future__ import annotations

import json
from pathlib import Path

from bankgpt.artifact.schema import Capability

ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = ROOT / "capabilities"


def load_capability(path: str | Path) -> Capability:
    p = Path(path)
    if not p.exists():
        alt = CAPABILITIES_DIR / path
        if not alt.suffix:
            alt = CAPABILITIES_DIR / f"{path}.json"
        p = alt
    data = json.loads(p.read_text())
    return Capability.model_validate(data)


def list_capabilities() -> list[Capability]:
    caps: list[Capability] = []
    if not CAPABILITIES_DIR.exists():
        return caps
    for path in sorted(CAPABILITIES_DIR.glob("*.json")):
        try:
            caps.append(load_capability(path))
        except Exception:
            continue
    return caps


def capability_tool(cap: Capability) -> dict:
    """OpenAI-style tool schema an agent can list and call by name."""
    properties = {}
    required = []
    for p in cap.spec.parameters:
        properties[p.name] = {
            "type": p.type if p.type in {"string", "number", "boolean"} else "string",
            "description": p.description or p.name,
        }
        if p.required:
            required.append(p.name)
    outputs = [{"name": o.name, "type": o.type} for o in cap.spec.outputs]
    return {
        "type": "function",
        "function": {
            "name": cap.metadata.id,
            "description": cap.metadata.description or cap.metadata.id,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
        "returns": outputs,
        "vendorApp": cap.metadata.vendor_app,
        "version": cap.metadata.version,
        "risk": cap.spec.policy.risk,
    }


def save_capability(cap: Capability, path: str | Path | None = None) -> Path:
    CAPABILITIES_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(path) if path else CAPABILITIES_DIR / f"{cap.metadata.id}.json"
    dest.write_text(cap.model_dump_json(by_alias=True, indent=2) + "\n")
    return dest
