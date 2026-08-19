from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

ApiVersion = Literal["bankgpt/v1"]
ActionName = Literal[
    "click",
    "fill",
    "select",
    "press",
    "dismiss",
    "wait",
    "extract",
    "navigate",
]
RiskClass = Literal["read", "write", "irreversible"]
RunStatus = Literal["success", "business_outcome", "failed", "escalated"]
Owner = Literal["automation", "human"]
LocatorBy = Literal["role", "label", "placeholder", "text", "table_cell", "nth"]


class Locator(BaseModel):
    by: LocatorBy
    role: str | None = None
    name: str | None = None
    text: str | None = None
    column: str | None = None
    index: int | None = None
    exact: bool = True


class Target(BaseModel):
    locators: list[Locator]


class Step(BaseModel):
    id: str
    action: ActionName
    target: Target | None = None
    value: str | None = None
    wait_ms: int | None = None


class Checkpoint(BaseModel):
    id: str
    after: str
    assert_target: Target = Field(alias="assert")

    model_config = {"populate_by_name": True}


class Detect(BaseModel):
    locators: list[Locator] | None = None
    busy: bool | None = None
    text: str | None = None


class Outcome(BaseModel):
    code: str
    kind: Literal["business_outcome"] = "business_outcome"
    detect: Detect
    after: str | None = None


class Recoverable(BaseModel):
    id: str
    detect: Detect
    action: Literal["dismiss", "wait_retry"]
    max_times: int = 2
    timeout_ms: int = 15000


class HardFailure(BaseModel):
    code: str
    detect: Detect
    escalate: bool = False


class Parameter(BaseModel):
    name: str
    type: str = "string"
    required: bool = True
    sensitive: bool = False
    description: str = ""


class OutputSpec(BaseModel):
    name: str
    type: str = "string"
    description: str = ""


class ExtractSpec(BaseModel):
    output: str
    from_target: Target = Field(alias="from")
    transform: str | None = None

    model_config = {"populate_by_name": True}


class PolicySpec(BaseModel):
    risk: RiskClass = "read"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    allowed_actions: list[ActionName] = Field(
        default_factory=lambda: [
            "click",
            "fill",
            "select",
            "press",
            "dismiss",
            "wait",
            "extract",
            "navigate",
        ]
    )


class CapabilityMetadata(BaseModel):
    id: str
    version: int = 1
    vendor_app: str = Field(alias="vendorApp", default="core-console")
    description: str = ""
    recorded_on: dict[str, Any] | None = Field(alias="recordedOn", default=None)

    model_config = {"populate_by_name": True}


class CapabilitySpec(BaseModel):
    parameters: list[Parameter] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    policy: PolicySpec = Field(default_factory=PolicySpec)
    entry_path: str = "/login"
    credentials_from_env: bool = True
    steps: list[Step]
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    recoverables: list[Recoverable] = Field(default_factory=list)
    hard_failures: list[HardFailure] = Field(alias="hardFailures", default_factory=list)
    extract: list[ExtractSpec] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class Capability(BaseModel):
    api_version: ApiVersion = Field(alias="apiVersion", default="bankgpt/v1")
    kind: Literal["Capability"] = "Capability"
    metadata: CapabilityMetadata
    spec: CapabilitySpec

    model_config = {"populate_by_name": True}


class DebugInfo(BaseModel):
    run_id: str
    failed_step: str | None = None
    expected: str | None = None
    observed: str | None = None
    evidence_dir: str | None = None


class RunResult(BaseModel):
    status: RunStatus
    capability: str
    version: int
    outcome_code: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    debug: DebugInfo


class InterventionRequest(BaseModel):
    run_id: str
    mode: str
    capability: str | None
    goal: str | None
    step_id: str | None
    reason: str
    screenshot: str | None = None
    snapshot_excerpt: str | None = None


PARAM_RE = re.compile(r"\{\{params\.([a-zA-Z0-9_]+)\}\}")


def render_value(template: str | None, params: dict[str, str]) -> str | None:
    if template is None:
        return None

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"missing parameter {key}")
        return params[key]

    return PARAM_RE.sub(repl, template)
