from __future__ import annotations

import json
import os
import time
from pathlib import Path

from bankgpt.artifact.schema import (
    Capability,
    DebugInfo,
    Detect,
    RunResult,
    Target,
    render_value,
)
from bankgpt.evidence import EvidenceLog
from bankgpt.policy.guard import PolicyPack, redact_text
from bankgpt.session import Session
from bankgpt.surface.web_a11y import WebA11yAdapter


class PolicyDenied(RuntimeError):
    pass


class EscalationNeeded(RuntimeError):
    def __init__(self, reason: str, step_id: str | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.step_id = step_id


def _match(adapter: WebA11yAdapter, detect: Detect) -> bool:
    return adapter.detect(detect.locators, detect.text, detect.busy)


class ReplayRunner:
    def __init__(
        self,
        session: Session,
        policy: PolicyPack,
        log: EvidenceLog,
        base_url: str,
        operator_wait: bool = False,
        operator_timeout_s: float = 300,
    ) -> None:
        self.session = session
        self.policy = policy
        self.log = log
        self.base_url = base_url.rstrip("/")
        self.operator_wait = operator_wait
        self.operator_timeout_s = operator_timeout_s

    def run(self, cap: Capability, params: dict[str, str]) -> RunResult:
        adapter = self.session.adapter or self.session.start()
        self._check_params(cap, params)
        entry = self.base_url + cap.spec.entry_path
        self._guard_nav(entry)
        adapter.act("navigate", None, entry)
        self.log.event("navigate", url=entry)
        if cap.spec.credentials_from_env:
            self._login(adapter)

        recover_counts: dict[str, int] = {}
        skip_ids: set[str] = set()
        steps = cap.spec.steps
        i = 0
        try:
            while i < len(steps):
                step = steps[i]
                if step.id in skip_ids:
                    self.log.event("skip", step=step.id, reason="completed_by_operator")
                    i += 1
                    continue
                try:
                    result = self._execute_step(cap, adapter, step, params, recover_counts)
                    if result is not None:
                        return result
                    i += 1
                except EscalationNeeded as esc:
                    handed = self._handoff(cap, adapter, esc)
                    if handed is not None:
                        return handed
                    if esc.step_id:
                        skip_ids.add(esc.step_id)
                    i += 1

            outputs: dict[str, str] = {}
            for ex in cap.spec.extract:
                raw = adapter.extract(ex.from_target, self.policy.step_timeout_ms)
                outputs[ex.output] = _transform(raw, ex.transform)
                self.log.event("extract", output=ex.output, value=outputs[ex.output])
            return RunResult(
                status="success",
                capability=cap.metadata.id,
                version=cap.metadata.version,
                outputs=outputs,
                debug=DebugInfo(run_id=self.session.run_id, evidence_dir=str(self.log.dir)),
            )
        except PolicyDenied as exc:
            snap = adapter.snapshot()
            self.session.screenshot("failure.png")
            return self._fail(cap, None, str(exc), snap.aria)
        except Exception as exc:
            snap = adapter.snapshot()
            self.session.screenshot("failure.png")
            self.log.write_json(
                "failure.a11y.json",
                {"aria": redact_text(snap.aria), "url": snap.url, "error": str(exc)},
            )
            return self._fail(cap, None, str(exc), snap.aria)

    def _execute_step(
        self,
        cap: Capability,
        adapter: WebA11yAdapter,
        step,
        params: dict[str, str],
        recover_counts: dict[str, int],
    ) -> RunResult | None:
        self._handle_recoverables(adapter, cap, recover_counts)
        hard = self._hard_failure(adapter, cap)
        if hard:
            return self._fail(cap, step.id, hard, adapter.snapshot().aria)
        if step.action == "fill":
            value = render_value(step.value, params)
            if value is None:
                raise ValueError(f"{step.id} fill missing value")
            self._guard_action("fill", adapter)
            logged = value
            if step.target:
                labels = " ".join(
                    (loc.name or loc.text or "") for loc in step.target.locators
                ).lower()
                if "password" in labels or "secret" in labels or "token" in labels:
                    logged = "[REDACTED:SECRET]"
            self.log.event("act", step=step.id, action="fill", value=logged)
            adapter.act("fill", step.target, value)
        elif step.action == "navigate":
            url = render_value(step.value, params) or ""
            self._guard_nav(url)
            adapter.act("navigate", None, url)
            self.log.event("act", step=step.id, action="navigate", url=url)
        else:
            self._guard_action(step.action, adapter)
            name = None
            if step.target and step.target.locators:
                name = step.target.locators[0].name
            if self.policy.is_irreversible_name(name):
                raise EscalationNeeded("irreversible action", step.id)
            self.log.event("act", step=step.id, action=step.action)
            adapter.act(step.action, step.target, render_value(step.value, params))

        if step.wait_ms:
            adapter.page.wait_for_timeout(step.wait_ms)  # type: ignore[union-attr]

        self._handle_recoverables(adapter, cap, recover_counts)
        hard = self._hard_failure(adapter, cap)
        if hard:
            return self._fail(cap, step.id, hard, adapter.snapshot().aria)
        outcome = self._outcome(adapter, cap, after=step.id)
        if outcome:
            self.log.event("outcome", code=outcome, step=step.id)
            return RunResult(
                status="business_outcome",
                capability=cap.metadata.id,
                version=cap.metadata.version,
                outcome_code=outcome,
                debug=DebugInfo(run_id=self.session.run_id, evidence_dir=str(self.log.dir)),
            )
        for cp in cap.spec.checkpoints:
            if cp.after == step.id:
                try:
                    adapter.find(cp.assert_target, timeout_ms=self.policy.step_timeout_ms)
                    self.log.event("checkpoint", id=cp.id, step=step.id)
                except LookupError:
                    outcome = self._outcome(adapter, cap, after=step.id)
                    if outcome:
                        return RunResult(
                            status="business_outcome",
                            capability=cap.metadata.id,
                            version=cap.metadata.version,
                            outcome_code=outcome,
                            debug=DebugInfo(
                                run_id=self.session.run_id, evidence_dir=str(self.log.dir)
                            ),
                        )
                    snap = adapter.snapshot()
                    self.session.screenshot("failure.png")
                    self.log.write_json(
                        "failure.a11y.json",
                        {"aria": redact_text(snap.aria), "url": snap.url},
                    )
                    return self._fail(cap, step.id, f"checkpoint {cp.id} missed", snap.aria)
        return None

    def _handoff(self, cap: Capability, adapter: WebA11yAdapter, esc: EscalationNeeded) -> RunResult | None:
        shot = self.session.screenshot("intervention.png")
        snap = adapter.snapshot()
        payload = {
            "run_id": self.session.run_id,
            "mode": "replay",
            "capability": cap.metadata.id,
            "step_id": esc.step_id,
            "reason": esc.reason,
            "screenshot": str(shot) if shot else None,
            "snapshot_excerpt": redact_text(snap.aria[:2000]),
        }
        self.log.write_json("intervention.json", payload)
        self.log.event("escalate", **payload)
        control = self.log.dir / "control.json"
        if not self.operator_wait:
            self.session.set_owner("human")
            return RunResult(
                status="escalated",
                capability=cap.metadata.id,
                version=cap.metadata.version,
                error=esc.reason,
                debug=DebugInfo(
                    run_id=self.session.run_id,
                    failed_step=esc.step_id,
                    expected="operator resume",
                    observed=esc.reason,
                    evidence_dir=str(self.log.dir),
                ),
            )
        if control.exists():
            control.unlink()
        self.session.enter_operator_mode(esc.reason)
        deadline = time.time() + self.operator_timeout_s
        print(
            f"\nEscalated — join the Playwright Chromium window (red bar at the top).\n"
            f"  Click Close account if you want, then Resume automation on that bar.\n"
            f"  Or: python -m bankgpt operator resume --run {self.session.run_id}\n"
            f"Waiting on {control}\n",
            flush=True,
        )
        while time.time() < deadline:
            cmd = self.session.poll_operator_command()
            if cmd == "abort":
                self.session.exit_operator_mode()
                return self._fail(cap, esc.step_id, "operator_abort", snap.aria)
            if cmd == "resume":
                actions = self.session.collect_human_actions()
                self.log.write_json("human_actions.json", {"actions": actions})
                self.log.event("resume", actor="human", human_action_count=len(actions))
                self.session.exit_operator_mode()
                self.session.screenshot("after_handoff.png")
                return None
        self.session.exit_operator_mode()
        return self._fail(cap, esc.step_id, "operator_timeout", snap.aria)

    def _handle_recoverables(self, adapter: WebA11yAdapter, cap: Capability, counts: dict[str, int]) -> None:
        for rec in cap.spec.recoverables:
            if not _match(adapter, rec.detect):
                continue
            counts[rec.id] = counts.get(rec.id, 0) + 1
            if counts[rec.id] > rec.max_times:
                raise EscalationNeeded(f"recoverable {rec.id} exceeded maxTimes", rec.id)
            self.log.event("recoverable", id=rec.id, action=rec.action)
            if rec.action == "dismiss":
                try:
                    adapter.page.get_by_role("button", name="OK").click(timeout=2000)  # type: ignore[union-attr]
                except Exception:
                    if rec.detect.locators:
                        adapter.act("dismiss", Target(locators=rec.detect.locators), None)
            elif rec.action == "wait_retry":
                adapter.page.wait_for_timeout(min(rec.timeout_ms, 2000))  # type: ignore

    def _hard_failure(self, adapter: WebA11yAdapter, cap: Capability) -> str | None:
        for hf in cap.spec.hard_failures:
            if _match(adapter, hf.detect):
                if hf.escalate:
                    raise EscalationNeeded(hf.code, None)
                return hf.code
        return None

    def _outcome(self, adapter: WebA11yAdapter, cap: Capability, after: str | None) -> str | None:
        for oc in cap.spec.outcomes:
            if oc.after and after and oc.after != after:
                continue
            if oc.after and after is None:
                continue
            if _match(adapter, oc.detect):
                return oc.code
        return None

    def _guard_nav(self, url: str) -> None:
        if not self.policy.host_allowed(url):
            raise PolicyDenied(f"host not allowlisted: {url}")

    def _guard_action(self, action: str, adapter: WebA11yAdapter) -> None:
        if not self.policy.action_allowed(action):
            raise PolicyDenied(f"action not allowlisted: {action}")
        if not self.policy.host_allowed(adapter.current_url()):
            raise PolicyDenied(f"host not allowlisted: {adapter.current_url()}")

    def _login(self, adapter: WebA11yAdapter) -> None:
        from bankgpt.artifact.schema import Locator, Target

        user = os.environ.get("BANKGPT_OPERATOR_ID", "teller")
        password = os.environ.get("BANKGPT_OPERATOR_PASSWORD", "demo")
        if adapter.text_present("Member search") or adapter.text_present("Member ID"):
            return
        adapter.act(
            "fill",
            Target(locators=[Locator(by="label", text="Operator ID")]),
            user,
        )
        adapter.act(
            "fill",
            Target(locators=[Locator(by="label", text="Password")]),
            password,
        )
        self.log.event("act", step="login", action="fill", value="[REDACTED:SECRET]")
        adapter.act(
            "click",
            Target(locators=[Locator(by="role", role="button", name="Sign on")]),
            None,
        )
        adapter.page.wait_for_url("**/search**", timeout=8000)  # type: ignore[union-attr]

    def _check_params(self, cap: Capability, params: dict[str, str]) -> None:
        for p in cap.spec.parameters:
            if p.required and p.name not in params:
                raise ValueError(f"missing parameter {p.name}")

    def _fail(self, cap: Capability, step: str | None, error: str, observed: str) -> RunResult:
        self.log.event("fail", step=step, error=error)
        return RunResult(
            status="failed",
            capability=cap.metadata.id,
            version=cap.metadata.version,
            error=error,
            debug=DebugInfo(
                run_id=self.session.run_id,
                failed_step=step,
                expected=None,
                observed=redact_text(observed[:1500]),
                evidence_dir=str(self.log.dir),
            ),
        )


def _transform(raw: str, kind: str | None) -> str:
    text = raw.replace("$", "").strip()
    if kind == "money":
        return text.replace(",", "")
    return text
