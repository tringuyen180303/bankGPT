# Evidence

Start Core Console first: `python -m core_console.app`.

| Folder | What it shows |
|---|---|
| `replay-success/` | `memberId=12345` → `status: success` |
| `replay-not-found/` | `memberId=99999` → `business_outcome` `MEMBER_NOT_FOUND` |
| `replay-escalated/` | Close-account step → `escalated` + `intervention.json` |
| `hitl-join/` | Headed handoff: resume on the live session, `human_actions.json` |

Each run has `run.jsonl` and usually `result.json`. Discovery runs are `evidence/discover-<id>/` (JSONL + compiled `artifact.json`). A genuine LLM discover is required for submission; replay-only folders are not enough by themselves.
