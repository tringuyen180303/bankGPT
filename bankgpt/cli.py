from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from bankgpt.artifact.schema import RunResult
from bankgpt.artifact.store import load_capability
from bankgpt.discovery.runner import DiscoveryRunner, Escalated
from bankgpt.evidence import EvidenceLog
from bankgpt.policy.guard import load_policy
from bankgpt.replay.runner import ReplayRunner
from bankgpt.session import Session

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policy" / "core-console.yaml"


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="bankgpt")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_disc = sub.add_parser("discover", help="LLM-driven recording")
    p_disc.add_argument("--goal", required=True)
    p_disc.add_argument("--target", default=os.environ.get("CORE_CONSOLE_URL", "http://127.0.0.1:3000"))
    p_disc.add_argument("--id", default="lookup-member-savings")
    p_disc.add_argument("--headed", action="store_true")
    p_disc.add_argument("--wait-operator", action="store_true")
    p_disc.add_argument("--max-steps", type=int, default=None)

    p_rep = sub.add_parser("replay", help="Deterministic replay (no LLM)")
    p_rep.add_argument("--capability", default="lookup-member-savings")
    p_rep.add_argument("--param", action="append", default=[], help="key=value")
    p_rep.add_argument("--target", default=os.environ.get("CORE_CONSOLE_URL", "http://127.0.0.1:3000"))
    p_rep.add_argument("--headed", action="store_true")
    p_rep.add_argument("--wait-operator", action="store_true")
    p_rep.add_argument("--times", type=int, default=1, help="replay N times (stability stretch)")
    p_rep.add_argument("--evidence-name", default=None)

    p_cat = sub.add_parser("catalog", help="List capabilities as agent tools")
    p_inv = sub.add_parser("invoke", help="Invoke a catalog capability (alias of replay)")
    p_inv.add_argument("--capability", required=True)
    p_inv.add_argument("--param", action="append", default=[])
    p_inv.add_argument("--target", default=os.environ.get("CORE_CONSOLE_URL", "http://127.0.0.1:3000"))
    p_inv.add_argument("--headed", action="store_true")
    p_inv.add_argument("--wait-operator", action="store_true")
    p_inv.add_argument("--evidence-name", default=None)
    p_inv.add_argument("--times", type=int, default=1)

    p_op = sub.add_parser("operator")
    op_sub = p_op.add_subparsers(dest="op", required=True)
    r = op_sub.add_parser("resume")
    r.add_argument("--run", required=True)
    a = op_sub.add_parser("abort")
    a.add_argument("--run", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "discover":
        _discover(args)
    elif args.cmd == "replay" or args.cmd == "invoke":
        _replay(args)
    elif args.cmd == "catalog":
        _catalog()
    elif args.cmd == "operator":
        _operator(args)


def _policy():
    return load_policy(str(DEFAULT_POLICY))


def _headed(flag: bool) -> bool:
    if flag:
        return True
    return os.environ.get("HEADLESS", "true").lower() in {"0", "false", "no"}


def _discover(args: argparse.Namespace) -> None:
    run_id = f"discover-{uuid.uuid4().hex[:8]}"
    log = EvidenceLog(run_id, subdir=run_id)
    session = Session(run_id=run_id, evidence_dir=log.dir, headed=_headed(args.headed))
    try:
        policy = _policy()
        if args.max_steps:
            policy.max_steps = args.max_steps
        runner = DiscoveryRunner(
            session, policy, log, args.target, operator_wait=args.wait_operator
        )
        cap = runner.run(args.goal, cap_id=args.id)
        print(cap.model_dump_json(by_alias=True, indent=2))
        print(f"evidence: {log.dir}")
    except Escalated as esc:
        print(f"escalated: {esc.reason}")
        print(f"evidence: {esc.evidence_dir}")
        raise SystemExit(2)
    finally:
        session.close()


def _catalog() -> None:
    from bankgpt.artifact.store import capability_tool, list_capabilities

    tools = [capability_tool(c) for c in list_capabilities()]
    print(json.dumps({"tools": tools}, indent=2))


def _replay(args: argparse.Namespace) -> None:
    params = {}
    for item in args.param:
        k, _, v = item.partition("=")
        params[k] = v
    cap = load_capability(args.capability)
    times = max(1, getattr(args, "times", 1) or 1)
    results = []
    failed = False
    for i in range(times):
        run_id = args.evidence_name or f"replay-{uuid.uuid4().hex[:8]}"
        if times > 1:
            run_id = f"{run_id}-n{i+1}"
        log = EvidenceLog(run_id, subdir=run_id)
        session = Session(run_id=run_id, evidence_dir=log.dir, headed=_headed(args.headed))
        try:
            runner = ReplayRunner(
                session, _policy(), log, args.target, operator_wait=args.wait_operator
            )
            result: RunResult = runner.run(cap, params)
            log.write_json("result.json", json.loads(result.model_dump_json()))
            results.append(
                {
                    "n": i + 1,
                    "status": result.status,
                    "outcome_code": result.outcome_code,
                    "error": result.error,
                    "evidence": str(log.dir),
                }
            )
            print(result.model_dump_json(indent=2))
            print(f"evidence: {log.dir}")
            if result.status == "failed":
                failed = True
        finally:
            session.close()
    if times > 1:
        ok = sum(1 for r in results if r["status"] in {"success", "business_outcome", "escalated"})
        summary = {
            "capability": cap.metadata.id,
            "times": times,
            "stable": ok,
            "failed": times - ok,
            "rate": round(ok / times, 3),
            "runs": results,
        }
        print(json.dumps(summary, indent=2))
        EvidenceLog(args.evidence_name or "stability", subdir=args.evidence_name or "stability").write_json(
            "stability.json", summary
        )
    if failed:
        raise SystemExit(1)


def _operator(args: argparse.Namespace) -> None:
    from bankgpt.evidence import EVIDENCE_ROOT

    dest = EVIDENCE_ROOT / args.run / "control.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"command": args.op}) + "\n")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
