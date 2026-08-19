from __future__ import annotations

import json
import os
import time
from typing import Any

from bankgpt.artifact.schema import Locator, Target
from bankgpt.artifact.store import save_capability
from bankgpt.discovery.compiler import _coerce_outputs, compile_capability
from bankgpt.discovery.llm import LLMClient, system_prompt
from bankgpt.evidence import EvidenceLog
from bankgpt.policy.guard import PolicyPack, redact_text
from bankgpt.session import Session
from bankgpt.surface.web_a11y import WebA11yAdapter


class Escalated(Exception):
    def __init__(self, reason: str, evidence_dir: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence_dir = evidence_dir


class DiscoveryRunner:
    def __init__(
        self,
        session: Session,
        policy: PolicyPack,
        log: EvidenceLog,
        base_url: str,
        operator_wait: bool = False,
    ) -> None:
        self.session = session
        self.policy = policy
        self.log = log
        self.base_url = base_url.rstrip("/")
        self.operator_wait = operator_wait
        self.llm = LLMClient()
        print(f"[discover] provider={self.llm.provider} model={self.llm.model}", flush=True)

    def run(self, goal: str, cap_id: str = "lookup-member-savings") -> Any:
        adapter = self.session.adapter or self.session.start()
        login = f"{self.base_url}/login"
        if not self.policy.host_allowed(login):
            raise RuntimeError("target host not allowlisted")
        adapter.act("navigate", None, login)
        self._login(adapter)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": f"Goal: {goal}"},
        ]
        trace: list[dict] = []
        outputs: dict[str, str] = {}
        last_step: dict[str, Any] | None = None

        for i in range(self.policy.max_steps):
            self._dismiss_notices(adapter)
            snap = adapter.snapshot()
            self.log.event("snapshot", url=snap.url, aria=redact_text(snap.aria[:4000]))
            if "no record found" in (snap.aria or "").lower():
                print("[discover] business outcome MEMBER_NOT_FOUND — stopping", flush=True)
                outputs = {"outcome": "MEMBER_NOT_FOUND"}
                cap = compile_capability(
                    cap_id,
                    goal,
                    "memberId",
                    _infer_member_id(goal),
                    trace,
                    outputs,
                )
                path = save_capability(cap)
                self.log.write_json("artifact.json", json.loads(path.read_text()))
                self.log.event("compiled", path=str(path), outcome="MEMBER_NOT_FOUND")
                return cap
            coach = _coach(snap, goal, last_step)
            print(f"[discover] coach: {coach}", flush=True)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Goal: {goal}\n"
                        f"Coach (do this now): {coach}\n"
                        f"Current URL: {snap.url}\n"
                        f"Title: {snap.title}\n"
                        f"Accessibility tree:\n{redact_text(snap.aria)[:6000]}"
                    ),
                }
            )
            tool = self.llm.next_tool(messages)
            if tool["name"] == "_fallback":
                tool = _tool_from_coach(coach)
                print(f"[discover] model skipped tools; using coach {tool['arguments']}", flush=True)
            print(f"[discover {i+1}/{self.policy.max_steps}] {tool['name']} {tool['arguments']}", flush=True)
            self.log.event("llm_tool", name=tool["name"], arguments=tool["arguments"])
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tool["id"],
                            "type": "function",
                            "function": {
                                "name": tool["name"],
                                "arguments": json.dumps(tool["arguments"]),
                            },
                        }
                    ],
                }
            )
            name = tool["name"]
            args = tool["arguments"]
            if name == "done":
                outputs = _coerce_outputs(args.get("outputs"))
                messages.append(
                    {"role": "tool", "tool_call_id": tool["id"], "content": "ok"}
                )
                cap = compile_capability(
                    cap_id,
                    goal,
                    "memberId",
                    _infer_member_id(goal),
                    trace,
                    outputs,
                )
                path = save_capability(cap)
                self.log.write_json("artifact.json", json.loads(path.read_text()))
                self.log.event("compiled", path=str(path))
                return cap
            if name == "stuck":
                self._escalate(goal, args.get("reason", "stuck"), f"s{i}", adapter)
            if name == "act":
                args = _normalize_act_args(
                    args, goal=goal, url=adapter.current_url()
                )
                print(f"[discover] normalized {args}", flush=True)
                action = args.get("action")
                if not self.policy.action_allowed(action):
                    self._escalate(goal, f"policy denied action {action}", f"s{i}", adapter)
                if not self.policy.host_allowed(adapter.current_url()):
                    self._escalate(goal, "navigated off allowlist", f"s{i}", adapter)
                if self.policy.is_irreversible_name(args.get("name") or args.get("text")):
                    self._escalate(goal, "irreversible", f"s{i}", adapter)
                    event = {
                        "id": _step_id(action, args, i),
                        "action": action,
                        "name": args.get("name"),
                        "actor": "human",
                        "rationale": "skipped; operator used live session",
                    }
                    trace.append(event)
                    last_step = event
                    self.log.event("act", **event)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool["id"],
                            "content": "operator completed this irreversible step; continue from current screen",
                        }
                    )
                    continue
                try:
                    target = _target_from_args(args)
                except Exception as exc:
                    self.session.screenshot(f"step-{i}.png")
                    self._escalate(goal, f"invalid locator from model: {exc}", f"s{i}", adapter)
                try:
                    adapter.act(action, target, args.get("value"), timeout_ms=3000)
                    result = "ok"
                except Exception as exc:
                    result = f"error: {exc}"
                    self.session.screenshot(f"step-{i}.png")
                    print(f"[discover] act failed (will retry unless stuck): {exc}", flush=True)
                event = {
                    "id": _step_id(action, args, i),
                    "action": action,
                    "by": args.get("by"),
                    "role": args.get("role"),
                    "name": args.get("name"),
                    "text": args.get("text"),
                    "value": args.get("value"),
                    "rationale": args.get("rationale"),
                }
                trace.append(event)
                last_step = event
                self.log.event("act", **{k: v for k, v in event.items() if k != "value" or action != "fill"})
                messages.append(
                    {"role": "tool", "tool_call_id": tool["id"], "content": result}
                )
                continue
            messages.append(
                {"role": "tool", "tool_call_id": tool["id"], "content": "unknown tool"}
            )
        self._escalate(goal, "max_steps", None, adapter)

    def _login(self, adapter: WebA11yAdapter) -> None:
        user = os.environ.get("BANKGPT_OPERATOR_ID", "teller")
        password = os.environ.get("BANKGPT_OPERATOR_PASSWORD", "demo")
        adapter.act("fill", Target(locators=[Locator(by="label", text="Operator ID")]), user)
        adapter.act("fill", Target(locators=[Locator(by="label", text="Password")]), password)
        adapter.act("click", Target(locators=[Locator(by="role", role="button", name="Sign on")]), None)
        adapter.page.wait_for_url("**/search**", timeout=8000)

    def _dismiss_notices(self, adapter: WebA11yAdapter) -> None:
        try:
            ok = adapter.page.get_by_role("button", name="OK")
            if ok.count():
                ok.first.click(timeout=800)
                print("[discover] dismissed System notice", flush=True)
        except Exception:
            return

    def _escalate(self, goal: str, reason: str, step_id: str | None, adapter: WebA11yAdapter) -> None:
        shot = self.session.screenshot("intervention.png")
        payload = {
            "run_id": self.session.run_id,
            "mode": "discover",
            "goal": goal,
            "step_id": step_id,
            "reason": reason,
            "screenshot": str(shot) if shot else None,
            "snapshot_excerpt": redact_text(adapter.snapshot().aria[:2000]),
        }
        self.log.write_json("intervention.json", payload)
        print(
            f"[escalate] {reason}\n"
            f"  Join the Playwright Chromium window (red bar) or:\n"
            f"  python -m bankgpt operator resume --run {self.session.run_id}",
            flush=True,
        )
        control = self.log.dir / "control.json"
        if self.operator_wait:
            if control.exists():
                control.unlink()
            self.session.enter_operator_mode(reason)
            deadline = time.time() + 300
            while time.time() < deadline:
                cmd = self.session.poll_operator_command()
                if cmd in {"resume", "abort"}:
                    actions = self.session.collect_human_actions()
                    self.log.write_json("human_actions.json", {"actions": actions})
                    self.log.event("resume", actor="human", command=cmd, human_action_count=len(actions))
                    self.session.exit_operator_mode()
                    if cmd == "abort":
                        raise Escalated("operator_abort", str(self.log.dir))
                    return
            self.session.exit_operator_mode()
        raise Escalated(reason, str(self.log.dir))


def _as_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("value", "name", "type", "by", "text"):
            if value.get(key):
                return str(value.get(key)).strip()
        return ""
    return str(value).strip()


def _normalize_act_args(args: dict, *, goal: str = "", url: str = "") -> dict:
    """Fix common small-model mistakes (empty names, typos, click-instead-of-fill)."""
    out = dict(args)
    if isinstance(out.get("by"), dict):
        blob = out["by"]
        out["by"] = _as_str(blob.get("type") or blob.get("by"))
        if not out.get("name") or str(out.get("name", "")).isdigit():
            label = blob.get("value") or blob.get("name") or blob.get("text")
            if label and not str(label).isdigit():
                if str(out.get("name", "")).isdigit() and not out.get("value"):
                    out["value"] = out["name"]
                out["name"] = str(label)
                out["text"] = str(label)
    rationale = _as_str(out.get("rationale")).lower()
    name = _as_str(out.get("name") or out.get("text"))
    if name.lower() == "memder id":
        name = "Member ID"
        out["name"] = name
    role = _as_str(out.get("role"))
    action = _as_str(out.get("action") or "click") or "click"
    value = out.get("value")
    if isinstance(value, dict):
        value = _as_str(value)
        out["value"] = value
    lowered = name.lower()
    url_l = url.lower()

    if action == "fill" and name.isdigit():
        out["value"] = name
        value = name
        name = "Member ID"
        lowered = "member id"
        out["name"] = "Member ID"
        out["text"] = "Member ID"

    if action == "fill" and not name:
        out["by"] = "label"
        out["role"] = "textbox"
        out["name"] = "Member ID"
        out["text"] = "Member ID"
        if not value:
            out["value"] = _infer_member_id(goal)
        return out
    if not name:
        if "search" in rationale or (
            action == "click" and "submit" in rationale and "sub-account" not in url_l
        ):
            name, lowered = "Search", "search"
            out["name"] = "Search"
        elif "continue" in rationale:
            name, lowered = "Continue", "continue"
            out["name"] = "Continue"
        elif "submit" in rationale and "sub-account" in url_l:
            name, lowered = "Submit", "submit"
            out["name"] = "Submit"
        elif "open sub" in rationale:
            name, lowered = "Open sub-account", "open sub-account"
            out["name"] = "Open sub-account"

    if lowered in {"search", "search button"} or role.lower() in {"search button"}:
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "button"
        out["name"] = "Search"
        out["text"] = "Search"
        return out
    if "member" in lowered and "id" in lowered:
        out["by"] = "label"
        out["role"] = "textbox"
        out["name"] = "Member ID"
        out["text"] = "Member ID"
        out["action"] = "fill"
        if not value:
            out["value"] = _infer_member_id(goal)
        return out
    if "open sub-account" in lowered:
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "link"
        out["name"] = "Open sub-account"
        return out
    if "post payment" in lowered:
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "link"
        out["name"] = "Post payment"
        return out
    if "draw on line" in lowered or lowered == "draw on line":
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "link"
        out["name"] = "Draw on line"
        return out
    if "close account" in lowered:
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "link"
        out["name"] = "Close account"
        return out
    if lowered in {"new search", "back to member"}:
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "link"
        out["name"] = "New search" if "search" in lowered else "Back to member"
        return out
    if lowered in {"continue", "submit", "ok", "sign on"}:
        out["action"] = "click"
        out["by"] = "role"
        out["role"] = "button"
        out["name"] = name.title() if lowered != "ok" else "OK"
        if lowered == "continue":
            out["name"] = "Continue"
        if lowered == "submit":
            out["name"] = "Submit"
        return out
    if lowered == "nickname" or "nickname" in lowered:
        out["action"] = "fill"
        out["by"] = "label"
        out["text"] = "Nickname"
        out["name"] = "Nickname"
        if not out.get("value"):
            out["value"] = "Travel"
        return out
        out["action"] = "select"
        out["by"] = "label"
        out["text"] = "Product type"
        out["value"] = value or "SAVINGS"
        return out

    if action == "click" and value:
        out["action"] = "fill"
        action = "fill"
    by = _as_str(out.get("by")).lower()
    if by in {"link", "heading", "button", "textbox", "dialog"}:
        out["role"] = role or by
        out["by"] = "role"
    elif by == "role" and not out.get("role"):
        looks_like_link = any(
            w in lowered for w in ("account", "payment", "draw", "search", "back")
        )
        out["role"] = "link" if looks_like_link else "button"
    elif by and by not in {"role", "label", "placeholder", "text", "table_cell", "nth"}:
        out["by"] = "role"
        out["role"] = out.get("role") or "button"
    return out


def _target_from_args(args: dict) -> Target | None:
    by = _as_str(args.get("by")) or ("role" if args.get("role") else "text")
    if by not in {"role", "label", "placeholder", "text", "table_cell", "nth"}:
        by = "role"
    loc = Locator(by=by, role=args.get("role"), name=args.get("name"), text=args.get("text"))
    if not (loc.name or loc.text or loc.role):
        return None
    return Target(locators=[loc])


def _step_id(action: str, args: dict, i: int) -> str:
    name = args.get("name") or args.get("text") or ""
    if action == "fill" and "Member" in name:
        return "fill_member_id"
    if action == "click" and name == "Search":
        return "submit_search"
    return f"s{i}"


def _tool_from_coach(coach: str) -> dict[str, Any]:
    import re
    import uuid

    call_id = f"coach-{uuid.uuid4().hex[:8]}"
    if coach.lower().startswith("call tool done") or "call done" in coach.lower():
        return {
            "id": call_id,
            "name": "done",
            "arguments": {"rationale": coach, "outputs": {}},
        }
    name_m = re.search(r'name="([^"]+)"', coach)
    value_m = re.search(r'value="([^"]+)"', coach)
    name = name_m.group(1) if name_m else "Search"
    args: dict[str, Any] = {"action": "click", "name": name, "rationale": "coach"}
    if "fill" in coach:
        args["action"] = "fill"
        args["by"] = "label"
        if value_m:
            args["value"] = value_m.group(1)
    elif "role=link" in coach or "link" in coach.lower():
        args["by"] = "role"
        args["role"] = "link"
    else:
        args["by"] = "role"
        args["role"] = "button"
    return {"id": call_id, "name": "act", "arguments": args}


def _goal_kind(goal: str) -> str:
    g = goal.lower()
    if "sub-account" in g or "nickname" in g:
        return "sub_account"
    if "payment" in g or "pay " in g:
        return "payment"
    if "draw" in g or "credit line" in g:
        return "draw"
    if "credit" in g:
        return "credit"
    return "lookup"


def _infer_amount(goal: str) -> str:
    import re

    m = re.search(r"(?:pay|payment|draw|amount)\D{0,20}(\d+(?:\.\d{1,2})?)", goal, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d+\.\d{2})\b", goal)
    return m.group(1) if m else "50.00"


def _coach(snap: Any, goal: str, last_step: dict | None = None) -> str:
    if os.environ.get("BANKGPT_COACH", "on").lower() in {"0", "off", "false", "no"}:
        return "Call one tool (act, done, or stuck) using names from the accessibility tree."
    aria = (snap.aria or "").lower()
    member = _infer_member_id(goal)
    kind = _goal_kind(goal)
    amount = _infer_amount(goal)
    last_name = ((last_step or {}).get("name") or "").lower()
    last_action = ((last_step or {}).get("action") or "").lower()
    if "sub-account opened" in aria or (
        "confirmation" in aria and "open sub-account" in aria and "nickname" not in aria
    ):
        return "Suggested: call done. outputs.confirmation = the code on screen."
    if "payment posted" in aria or "credit draw posted" in aria:
        return "Suggested: call done. outputs.confirmation = the code on screen."
    if "review and submit" in aria:
        return 'Suggested: act click button name="Submit".'
    if "payment amount" in aria:
        already = last_action == "fill" and "amount" in last_name
        if already:
            return 'Suggested: act click button name="Continue".'
        return f'Suggested: act fill by=label name="Payment amount" value="{amount}".'
    if "draw amount" in aria:
        already = last_action == "fill" and "amount" in last_name
        if already:
            return 'Suggested: act click button name="Continue".'
        return f'Suggested: act fill by=label name="Draw amount" value="{amount}".'
    if "nickname" in aria and "product type" in aria:
        already = last_action == "fill" and "nickname" in last_name
        if already or "travel" in aria:
            return 'Suggested: act click button name="Continue". Do not fill Nickname again.'
        return 'Suggested: act fill by=label name="Nickname" value="Travel".'
    if "no record found" in aria:
        return (
            "Call tool done now. This is business outcome MEMBER_NOT_FOUND. "
            "Do not fill Member ID. There is no Search field on this screen."
        )
    if "member detail" in aria:
        if kind == "sub_account":
            return 'Suggested: act click role=link name="Open sub-account".'
        if kind == "payment":
            return 'Suggested: act click role=link name="Post payment".'
        if kind == "draw":
            return 'Suggested: act click role=link name="Draw on line".'
        if kind == "credit":
            return "Suggested: call done with credit table outputs. Do not click other links."
        return (
            "Suggested: call done. outputs.savingsBalance = Savings balance. "
            "Do not click Open sub-account."
        )
    if "member id" in aria or "member search" in aria:
        if last_action == "fill" and "member" in last_name:
            return 'Suggested: act click button name="Search".'
        return f'Suggested: act fill by=label name="Member ID" value="{member}".'
    return (
        "Suggested: one act using a name on this screen "
        "(Search, Member ID, Post payment, Draw on line, Open sub-account, amount fields, Continue, Submit)."
    )


def _infer_member_id(goal: str) -> str:
    import re

    m = re.search(r"\b(\d{4,})\b", goal)
    return m.group(1) if m else "12345"
