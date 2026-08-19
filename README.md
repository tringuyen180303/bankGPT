# BankGPT

BankGPT is a computer-use layer for legacy bank screens that have no API. An LLM walks the UI once, that run is saved as a typed capability JSON file, and production replay executes the artifact without putting the model back in the decision loop.

This is a take-home vertical slice in the spirit of Interface.ai, not a product. Decisions are in [`REPORT.md`](REPORT.md). Longer design notes are in [`DESIGN.md`](DESIGN.md).

## Setup

You need Python 3.11 or newer. From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env
```

The demo Core Console logs in with operator ID `teller` and password `demo`. Those are not real secrets, but they still never get written into capability files. Override them with `BANKGPT_OPERATOR_ID` and `BANKGPT_OPERATOR_PASSWORD` if you need to.

## Replay without a live LLM

Start the local stand-in core in one terminal. It listens at `http://127.0.0.1:3000`:

```bash
python -m core_console.app
```

In a second terminal, activate the venv and replay a saved capability. Replay does not call a model; it walks the JSON against the live UI.

```bash
source .venv/bin/activate

# Happy path: look up member 12345 and read the savings balance.
python -m bankgpt replay --capability lookup-member-savings --param memberId=12345 --evidence-name replay-success

# Member 99999 is not in the system. That is a business outcome, not a crash.
python -m bankgpt replay --capability lookup-member-savings --param memberId=99999 --evidence-name replay-not-found

# Close account is irreversible, so replay escalates instead of clicking it.
python -m bankgpt replay --capability close-account-teller --param memberId=12345 --evidence-name replay-escalated

# Member 11111 has no credit product; 12345 does.
python -m bankgpt replay --capability lookup-credit-line --param memberId=12345
python -m bankgpt replay --capability lookup-credit-line --param memberId=11111

# Post $50 from savings onto the loan. --headed opens Playwright Chromium, not your normal browser.
python -m bankgpt replay --capability post-payment --param memberId=12345 --param amount=50.00 --headed
```

Runs are headless by default (`HEADLESS=true` in `.env`). Pass `--headed` when you want to watch the browser.

Tests start their own Core Console on port 3010 so they do not collide with a demo on 3000:

```bash
pytest -q
```

## Discovery, then replay

Discovery needs Core Console running, plus either a funded OpenAI key or a local Ollama server:

```bash
ollama serve
```

Point `.env` at Ollama if you are staying local:

```
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_MODEL=llama3.2:latest
```

Then record a flow in natural language. `--goal` is the English instruction; `--id` is only the filename under `capabilities/`.

```bash
python -m bankgpt discover \
  --goal "Look up member 12345 and read the current savings balance" \
  --target http://127.0.0.1:3000 \
  --id lookup-member-savings \
  --headed

python -m bankgpt replay --capability lookup-member-savings --param memberId=12345
python -m bankgpt replay --capability lookup-member-savings --param memberId=99999
```

Other goals the demo core can complete:

```bash
python -m bankgpt discover --id lookup-credit-line --headed \
  --goal "Look up member 12345 and tell me the credit limit, available credit, and loan balance"

python -m bankgpt discover --id post-payment --headed \
  --goal "Find member 12345, open Post payment, and post a 100.00 loan payment from savings"

python -m bankgpt discover --id credit-draw --headed \
  --goal "Look up member 12345 and draw 100 from the credit line into savings"

python -m bankgpt discover --id open-savings-sub-account --headed \
  --goal "Open a savings sub-account for member 12345 named Travel and tell me the confirmation"
```

Discovery logs in from environment variables so passwords never land in the capability. After sign-on, the model drives the UI from an accessibility snapshot using the tools `act`, `done`, and `stuck`. Replay never calls the model.

## Catalog and stability

Saved artifacts show up as typed tools an agent can list and invoke. `invoke` uses the same engine as `replay`. `--times` reruns a capability and prints a pass rate; use a lookup for that, not a payment or draw, because those are not idempotent.

```bash
python -m bankgpt catalog

python -m bankgpt invoke --capability lookup-member-savings --param memberId=12345 --headed

python -m bankgpt replay --capability lookup-member-savings --param memberId=12345 --times 3
```

## Human in the loop

When automation cannot safely continue, it writes `evidence/<runId>/intervention.json` and waits. With `--headed --wait-operator`, the same Playwright window stays open. A red bar tells you that you are in control; **Resume automation** and **Abort** continue or stop the run. Clicks you make are stored in `human_actions.json`.

```bash
python -m bankgpt replay --capability close-account-teller --param memberId=12345 \
  --headed --wait-operator --evidence-name hitl-join
```

You can also resume or abort from a second terminal:

```bash
python -m bankgpt operator resume --run hitl-join
# or
python -m bankgpt operator abort --run hitl-join
```

`Session.owner` is either `automation` or `human`. Close account is never auto-clicked. The Core Console close screen is a permission denial (`You do not have access`), not a real delete.

## Layout

| Path | What it holds |
|---|---|
| `core_console/` | Local stand-in core: iframe search, tables, no test IDs, notice dialogs, not-found, denied close, payment and draw |
| `capabilities/` | Versioned JSON artifacts |
| `policy/core-console.yaml` | Host and action allowlist |
| `bankgpt/` | Session, accessibility adapter, replay, discovery, escalation, evidence |
| `evidence/` | Run logs, results, traces, and human-in-the-loop files |
| `REPORT.md` | Required write-up |

## Safety

The allowlist is localhost only. Login comes from the environment, never from capability steps. Logs redact PAN- and SSN-like strings, password and token assignments, and demo member names. Close account is treated as irreversible. Screenshots and `trace.zip` can still show the live page, so treat evidence as sensitive.
