from __future__ import annotations

import json
import uuid
from pathlib import Path

from core_console.data import reset_members
from bankgpt.artifact.store import load_capability
from bankgpt.evidence import EvidenceLog
from bankgpt.policy.guard import load_policy, redact_text
from bankgpt.replay.runner import ReplayRunner
from bankgpt.session import Session

POLICY = Path(__file__).resolve().parents[1] / "policy" / "core-console.yaml"


def _run(console_url: str, cap_id: str, params: dict[str, str], name: str):
    reset_members()
    cap = load_capability(cap_id)
    log = EvidenceLog(name, subdir=name)
    session = Session(run_id=name, evidence_dir=log.dir, headed=False)
    try:
        runner = ReplayRunner(session, load_policy(str(POLICY)), log, console_url)
        result = runner.run(cap, params)
        log.write_json("result.json", json.loads(result.model_dump_json()))
        return result
    finally:
        session.close()


def test_replay_success(console_url: str) -> None:
    result = _run(console_url, "lookup-member-savings", {"memberId": "12345"}, f"test-ok-{uuid.uuid4().hex[:6]}")
    assert result.status == "success"
    assert result.outputs["savingsBalance"] == "1240.50"


def test_replay_not_found_is_business_outcome(console_url: str) -> None:
    result = _run(console_url, "lookup-member-savings", {"memberId": "99999"}, f"test-nf-{uuid.uuid4().hex[:6]}")
    assert result.status == "business_outcome"
    assert result.outcome_code == "MEMBER_NOT_FOUND"


def test_irreversible_escalates(console_url: str) -> None:
    result = _run(console_url, "close-account-teller", {"memberId": "12345"}, f"test-esc-{uuid.uuid4().hex[:6]}")
    assert result.status == "escalated"


def test_redact_pan() -> None:
    assert "[REDACTED:PAN]" in redact_text("card 4111111111111111 ok")
    assert "[REDACTED:NAME]" in redact_text("Name Alex Rivera")
    assert "[REDACTED:SECRET]" in redact_text("password: hunter2")


def test_lookup_credit_line(console_url: str) -> None:
    result = _run(console_url, "lookup-credit-line", {"memberId": "12345"}, f"test-cl-{uuid.uuid4().hex[:6]}")
    assert result.status == "success"
    assert result.outputs["creditLimit"] == "5000.00"
    assert result.outputs["availableCredit"] == "2200.00"
    assert result.outputs["loanBalance"] == "2800.00"


def test_lookup_credit_no_product(console_url: str) -> None:
    result = _run(console_url, "lookup-credit-line", {"memberId": "11111"}, f"test-np-{uuid.uuid4().hex[:6]}")
    assert result.status == "business_outcome"
    assert result.outcome_code == "NO_CREDIT_PRODUCT"


def test_post_payment(console_url: str) -> None:
    result = _run(
        console_url,
        "post-payment",
        {"memberId": "12345", "amount": "50.00"},
        f"test-pay-{uuid.uuid4().hex[:6]}",
    )
    assert result.status == "success"
    assert result.outputs["confirmation"]


def test_credit_draw_insufficient(console_url: str) -> None:
    result = _run(
        console_url,
        "credit-draw",
        {"memberId": "12345", "amount": "9999"},
        f"test-dr-{uuid.uuid4().hex[:6]}",
    )
    assert result.status == "business_outcome"
    assert result.outcome_code == "INSUFFICIENT_AVAILABLE_CREDIT"
