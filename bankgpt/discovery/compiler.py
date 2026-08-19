from __future__ import annotations

import json
from datetime import datetime, timezone

from bankgpt.artifact.schema import (
    Capability,
    CapabilityMetadata,
    CapabilitySpec,
    Checkpoint,
    ExtractSpec,
    Locator,
    Outcome,
    OutputSpec,
    Parameter,
    PolicySpec,
    Recoverable,
    Step,
    Target,
    Detect,
    HardFailure,
)


VENDOR_OUTCOMES = [
    Outcome(
        code="MEMBER_NOT_FOUND",
        detect=Detect(
            locators=[Locator(by="role", role="status", name="No record found")],
            text="No record found",
        ),
        after="submit_search",
    ),
    Outcome(
        code="PERMISSION_DENIED",
        detect=Detect(text="You do not have access"),
    ),
]

VENDOR_RECOVERABLES = [
    Recoverable(
        id="system_notice",
        detect=Detect(locators=[Locator(by="role", role="dialog", name="System notice")]),
        action="dismiss",
    )
]

VENDOR_HARD = [
    HardFailure(code="SESSION_EXPIRED", detect=Detect(text="Session expired"), escalate=True)
]


def compile_capability(
    cap_id: str,
    description: str,
    param_name: str,
    param_example: str,
    trace: list[dict],
    outputs: dict[str, str],
) -> Capability:
    steps: list[Step] = []
    for i, event in enumerate(trace):
        action = event.get("action")
        if action in {"navigate", "wait"} and event.get("step") == "login":
            continue
        locators = _locators_from_event(event)
        value = event.get("value")
        if value and param_example and value == param_example:
            value = f"{{{{params.{param_name}}}}}"
        if action == "fill" and event.get("name") in {"Password", "Operator ID"}:
            continue
        if action == "click" and event.get("name") == "Sign on":
            continue
        sid = event.get("id") or f"s{i}"
        steps.append(
            Step(
                id=sid,
                action=action,
                target=Target(locators=locators) if locators else None,
                value=value,
            )
        )

    extract = []
    output_specs = []
    outputs = _coerce_outputs(outputs)
    for name, _val in outputs.items():
        output_specs.append(OutputSpec(name=name, type="money" if "balance" in name.lower() else "string"))
        extract.append(
            ExtractSpec(
                output=name,
                **{
                    "from": Target(
                        locators=[
                            Locator(by="table_cell", column="Savings balance"),
                            Locator(by="text", text="Savings balance"),
                        ]
                    )
                },
                transform="money" if "balance" in name.lower() else None,
            )
        )

    if not any(s.id == "submit_search" for s in steps):
        for s in steps:
            if s.action == "click" and s.target:
                names = [loc.name or loc.text for loc in s.target.locators]
                if "Search" in names:
                    s.id = "submit_search"

    return Capability(
        metadata=CapabilityMetadata(
            id=cap_id,
            version=1,
            vendorApp="core-console",
            description=description,
            recordedOn={"tenant": "demo", "at": datetime.now(timezone.utc).isoformat()},
        ),
        spec=CapabilitySpec(
            parameters=[
                Parameter(name=param_name, type="string", description="Member number on the search screen")
            ],
            outputs=output_specs,
            policy=PolicySpec(risk="read"),
            steps=steps or _default_lookup_steps(param_name),
            checkpoints=[
                Checkpoint(
                    id="on_member_detail",
                    after="submit_search",
                    **{"assert": Target(locators=[Locator(by="role", role="heading", name="Member detail")])},
                )
            ],
            outcomes=VENDOR_OUTCOMES,
            recoverables=VENDOR_RECOVERABLES,
            hardFailures=VENDOR_HARD,
            extract=extract
            or [
                ExtractSpec(
                    output="savingsBalance",
                    **{
                        "from": Target(
                            locators=[Locator(by="table_cell", column="Savings balance")]
                        )
                    },
                    transform="money",
                )
            ],
        ),
    )


def _coerce_outputs(outputs: object) -> dict[str, str]:
    if outputs is None:
        return {}
    if isinstance(outputs, str):
        text = outputs.strip()
        try:
            outputs = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text} if text else {}
    if not isinstance(outputs, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in outputs.items()}


def _default_lookup_steps(param_name: str) -> list[Step]:
    return [
        Step(
            id="fill_member_id",
            action="fill",
            target=Target(
                locators=[
                    Locator(by="role", role="textbox", name="Member ID"),
                    Locator(by="label", text="Member ID"),
                ]
            ),
            value=f"{{{{params.{param_name}}}}}",
        ),
        Step(
            id="submit_search",
            action="click",
            target=Target(locators=[Locator(by="role", role="button", name="Search")]),
        ),
    ]


def _locators_from_event(event: dict) -> list[Locator]:
    by = event.get("by") or "role"
    locators = []
    if event.get("name") or event.get("role") or event.get("text"):
        locators.append(
            Locator(
                by=by if by in {"role", "label", "placeholder", "text"} else "role",
                role=event.get("role"),
                name=event.get("name"),
                text=event.get("text"),
            )
        )
        if event.get("name") and by == "role":
            locators.append(Locator(by="label", text=event.get("name")))
            locators.append(Locator(by="text", text=event.get("name")))
    return locators
