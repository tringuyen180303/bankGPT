from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from bankgpt.artifact.schema import ActionName

PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SECRET_ASSIGN_RE = re.compile(
    r"(?i)(\b(?:password|passwd|secret|token|authorization)\s*[:=]\s*)\S+"
)
# Demo core names — treat like production PII in artifacts/logs.
NAME_RES = [
    re.compile(r"Alex Rivera", re.I),
    re.compile(r"Jordan Lee", re.I),
]
PASSWORD_KEYS = {"password", "passwd", "token", "secret", "authorization", "cookie", "operator_id"}


class PolicyPack(BaseModel):
    id: str
    allowed_hosts: list[str]
    allowed_url_patterns: list[str] = Field(default_factory=list)
    allowed_actions: list[ActionName]
    denied_actions: list[str] = Field(default_factory=list)
    irreversible_names: list[str] = Field(default_factory=list)
    max_steps: int = 25
    step_timeout_ms: int = 10000

    def host_allowed(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        if host in self.allowed_hosts:
            return True
        return any(re.search(p, url) for p in self.allowed_url_patterns)

    def action_allowed(self, action: str) -> bool:
        if action in self.denied_actions:
            return False
        return action in self.allowed_actions

    def is_irreversible_name(self, name: str | None) -> bool:
        if not name:
            return False
        lowered = name.lower()
        return any(n.lower() in lowered for n in self.irreversible_names)


def load_policy(path: str) -> PolicyPack:
    data = yaml.safe_load(Path(path).read_text())
    return PolicyPack.model_validate(data)


def redact_text(value: str) -> str:
    value = PAN_RE.sub("[REDACTED:PAN]", value)
    value = SSN_RE.sub("[REDACTED:SSN]", value)
    value = SECRET_ASSIGN_RE.sub(r"\1[REDACTED:SECRET]", value)
    for pat in NAME_RES:
        value = pat.sub("[REDACTED:NAME]", value)
    return value


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in PASSWORD_KEYS:
                out[k] = "[REDACTED:SECRET]"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    return obj
