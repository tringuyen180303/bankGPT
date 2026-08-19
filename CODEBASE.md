# Codebase walkthrough

This is a guided tour of **what is actually in the repo** and how a request flows through it. Read this after (or instead of) `DESIGN.md` if you want to follow files, not just ideas.

Two programs live here:

1. **`core_console/`** — a fake bank UI we automate against (the “legacy core”).
2. **`bankgpt/`** — the computer-use system (discover, save a capability, replay, escalate).

They only talk through a browser, the same way a teller would: HTTP pages, clicks, typing. There is no Core Console API that BankGPT calls.

---

## 1. Map of the repo

```
bankGPT/
  core_console/          # dummy bank screens (FastAPI + HTML)
  bankgpt/               # the system under evaluation
    cli.py               # discover / replay / operator
    session.py           # one live Chromium + owner lock
    surface/web_a11y.py  # snapshot / find / click / type (not CSS)
    artifact/            # Capability schema + load/save JSON
    replay/runner.py     # production path: NO LLM
    discovery/           # LLM path: observe → act → compile artifact
    policy/guard.py      # allowlist + redaction
    evidence.py          # JSONL logs per run
  capabilities/          # saved artifacts (the “skills”)
  policy/core-console.yaml
  evidence/              # example runs
  tests/                 # replay against Core Console, no OpenAI
  DESIGN.md              # why we designed it this way
  REPORT.md              # short write-up for graders
```

**How to read it the first time**

1. `core_console/app.py` + templates — what the agent is clicking.
2. `capabilities/lookup-member-savings.json` — what a saved skill looks like.
3. `bankgpt/cli.py` → `replay/runner.py` → `surface/web_a11y.py` — the production path.
4. `discovery/runner.py` + `llm.py` + `compiler.py` — the one-time teaching path.
5. `session.py` + operator command in `cli.py` — human takeover.

---

## 2. The one picture that matters

```
You type a CLI command
        │
        ▼
   bankgpt/cli.py
        │  creates Session + EvidenceLog + loads policy YAML
        ├──────────────────┬─────────────────────┐
        ▼                  ▼                     ▼
  discover            replay                 operator
  (LLM)               (no LLM)               (write control.json)
        │                  │
        │                  │
        ▼                  ▼
 DiscoveryRunner      ReplayRunner
        │                  │
        └────────┬─────────┘
                 ▼
            Session          ← one Chromium window
                 │              owner = automation | human
                 ▼
         WebA11yAdapter      ← sees a11y tree, acts by role/name
                 │
                 ▼
          Core Console       ← localhost:3000 HTML
```

**Replay never imports `discovery.llm`.** If you grep `ReplayRunner`, there is no OpenAI client. That is the requirement “do not invoke the LLM for decisions.”

---

## 3. Core Console — the app we drive

**Files:** `core_console/app.py`, `core_console/data.py`, `core_console/templates/*.html`

This is **not** BankGPT. It is a tiny FastAPI site that pretends to be a 2004 operator workstation.

### Data (`data.py`)

Only one real member:

| ID | Result |
|---|---|
| `12345` | Alex Rivera, savings `1,240.50` |
| anything else (e.g. `99999`) | “No record found” |

Login: `teller` / `demo`.

### Routes (`app.py`)

| URL | Page | Why it exists |
|---|---|---|
| `GET/POST /login` | Operator sign-on | Practice not storing passwords in artifacts |
| `GET /search` | Outer page + **iframe** | Legacy frames; locators must search frames |
| `GET /search-frame` | Form: Member ID + Search | Lives inside the iframe |
| `GET /search-submit?member_id=` | Redirects to detail or not-found | The search POST/GET |
| `GET /member/{id}` | Table of fields + optional **dialog** | Happy path + recoverable interstitial |
| `GET /not-found` | `role="status"` “No record found” | **Business outcome**, not a 500 |
| `GET /close/{id}` | “You do not have access” | Permission / irreversible demo |
| `GET /sub-account/{id}` | Form → review → confirm | Write path (optional) |
| `GET /search?timeout=1` | Session expired | Hard failure |

On successful login the session sets `notice_pending = True`. The **first** member detail view opens a `<dialog aria-label="System notice">`. Replay must **dismiss** that, not crash.

Search is in an iframe on purpose. Playwright cannot `.or_()` locators from two frames; `WebA11yAdapter.find` tries the main page, then each iframe, separately.

### Templates

Ugly on purpose: nested `<table>`, **no `data-testid`**. Labels (`<label for="member_id">`) still exist so accessibility names work — that is the bet: even hostile markup often has roles/names a screen reader can use.

Start it: `python -m core_console.app` → `http://127.0.0.1:3000`.

---

## 4. The artifact (capability JSON)

**Files:** `capabilities/*.json`, types in `bankgpt/artifact/schema.py`, IO in `bankgpt/artifact/store.py`

The artifact is the **saved skill**. YAML in `DESIGN.md` was only for reading. On disk it is **JSON**.

`lookup-member-savings.json` means:

- Callable name: `lookup-member-savings` version 1
- Input: `memberId` (string)
- Output: `savingsBalance` (money)
- Login is **not** in the steps (`credentials_from_env: true`) — the runner logs in from env so passwords never sit in git
- Steps:
  1. `fill_member_id` — fill textbox “Member ID” with `{{params.memberId}}`
  2. `submit_search` — click button “Search”
- After search: if we see “No record found” → `MEMBER_NOT_FOUND`
- Else checkpoint: heading “Member detail”
- Extract savings from the table row “Savings balance”
- If a “System notice” dialog appears → dismiss (recoverable)
- If “Session expired” → hard failure

`store.py` loads that JSON into a Pydantic `Capability`. `{{params.memberId}}` is substituted by `render_value()` in `schema.py` at replay time.

`close-account-teller.json` is a second capability whose last step is a control named “Close account”. Policy treats that name as **irreversible**, so replay **escalates** instead of clicking.

---

## 5. CLI — the front door

**File:** `bankgpt/cli.py`  
Entry: `python -m bankgpt …` (`__main__.py` calls `main()`).

Three subcommands:

### `discover`

Creates a `run_id` like `discover-a1b2c3d4`, an `EvidenceLog` folder, a `Session` (Playwright), loads `policy/core-console.yaml`, runs `DiscoveryRunner`. Prints the compiled capability. Always `session.close()` in `finally` (stops tracing, closes browser).

### `replay`

Parses `--param memberId=12345` into a dict. `load_capability(...)` reads JSON. `ReplayRunner.run(cap, params)` returns a `RunResult`. Writes `result.json`. Exit code 1 only if `status == failed` — **not** for `business_outcome` (not-found is success-as-a-result).

### `operator resume|abort --run <id>`

Does **not** talk to Playwright. It writes `evidence/<runId>/control.json` `{"command": "resume"}`. The paused runner is polling that file. That is the HITL “signal.”

`--headed` shows the browser (needed if a human will click). Default is headless unless `HEADLESS=false`.

---

## 6. Session — the live computer

**File:** `bankgpt/session.py`

One object per run:

- Starts Playwright Chromium (`headless=not headed`)
- One `BrowserContext` + one `Page`
- Starts Playwright **tracing** (screenshots + snapshots → `trace.zip` on close)
- Wraps the page in `WebA11yAdapter`
- `owner`: `"automation"` or `"human"`

Replay checks `owner` before each step. If it is `human`, automation must not click. Escalation calls `set_owner("human")` and leaves the browser **open**. That is “same live session.”

There is no Temporal. The process **is** the workflow. If the process dies, the window dies. That is an accepted cut.

---

## 7. Surface adapter — eyes and hands

**File:** `bankgpt/surface/web_a11y.py`

This is the only module that knows “we are in a web page.” Replay and discovery both call it.

### Snapshot (observation)

`snapshot()` builds a `SurfaceSnapshot`: URL, title, and a compact **accessibility tree** (`aria_snapshot()` on `body`), plus each iframe. That string is what the **LLM** sees in discovery. It is **not** the raw DOM.

Example flavor:

```
heading "Member search"
iframe
  textbox "Member ID"
  button "Search"
```

### Find (replay targeting)

A `Target` is a **list** of locators. Try in order, on the main page then each iframe:

| `by` | Playwright call |
|---|---|
| `role` | `get_by_role("textbox", name="Member ID")` |
| `label` | `get_by_label("Member ID")` |
| `text` | `get_by_text("Search")` |
| `table_cell` | row containing column header, then `td` |
| `placeholder` / `nth` | fallbacks |

No CSS selectors in the capability. If all locators miss → `LookupError` → replay **fails** with observed a11y dump.

### Act

`click` / `fill` / `select` / `press` / `dismiss` / `wait` / `navigate`. `fill` types the already-substituted parameter.

### Detect

Used for outcomes (“No record found”) and recoverables (dialog). Looks at the a11y snapshot text first (fast), not a long locator wait.

`SurfaceAdapter` is a stub interface so a future desktop adapter could implement the same methods. Nothing else should import Playwright except session + this file.

---

## 8. Replay runner — production, no model

**File:** `bankgpt/replay/runner.py`

This is the heart of the take-home.

`run(capability, params)`:

1. Start session if needed.
2. Check required params exist.
3. `navigate` to `base_url + entry_path` (`/login`). Policy must allow the host.
4. If `credentials_from_env`: `_login()` fills Operator ID / Password from env (`teller`/`demo`), clicks Sign on, waits for `/search`. Password is logged as `[REDACTED:SECRET]`.
5. For **each step** in order:
   - If `owner != automation` → escalate/wait.
   - Handle recoverables (e.g. click OK on System notice).
   - If hard-failure matcher (session expired) → `status: failed` (or escalate if flagged).
   - Execute the step (`fill` substitutes `{{params.memberId}}`).
   - If the control **name** matches policy irreversible list (`Close account`, `Wire`, …) → **do not click**; raise `EscalationNeeded`.
   - After the step: recoverables again, hard failures, then **outcomes**. If `MEMBER_NOT_FOUND` matches → return `status: business_outcome` (not failed).
   - If a checkpoint is tied to this `step.id`, assert the heading/control exists. If it misses, try outcome again, else fail with screenshot + `failure.a11y.json`.
6. Run `extract` specs → `outputs` dict → `status: success`.

**Error taxonomy in code** (same as the brief):

| Result `status` | When |
|---|---|
| `success` | Checkpoints + extracts |
| `business_outcome` | Declared matcher (not-found, denied) |
| `failed` | Locator miss, checkpoint miss, policy, expired |
| `escalated` | Irreversible, stuck, or `--wait-operator` pause |

`_escalate` writes `intervention.json`, screenshot, sets owner to human. If `--wait-operator`, it polls `control.json` until resume/abort/timeout. Without that flag (default), it **returns** `escalated` immediately so CI/tests do not hang.

There is a leftover dead line (`after=step.id if False else None`) before the fill — it does nothing; outcomes are applied **after** the step.

---

## 9. Discovery — LLM teaches, compiler writes the skill

### `discovery/llm.py`

OpenAI chat with `tool_choice="required"`. Three tools only:

- `act` — one click/fill/etc with role/name
- `done` — goal met, optional outputs
- `stuck` — cannot continue safely

The model is **not** given raw HTML. The system prompt says: already signed on, prefer role+name, dismiss system notice, call `stuck` on Close account.

Needs `OPENAI_API_KEY`. Replay does not.

### `discovery/runner.py`

1. Navigate to login, `_login()` in code (same reason: no password in the artifact).
2. Loop up to `policy.max_steps`:
   - `snapshot()` → append a11y tree to messages (redacted)
   - `llm.next_tool()`
   - `done` → `compile_capability(trace)` → `save_capability` → return
   - `stuck` / irreversible / max steps → `_escalate`
   - `act` → policy check → `adapter.act` → append to `trace`
3. Each tool result goes back into the chat so the model sees “ok” or an error.

The **trace** is a list of dicts (action, role, name, value). That is still not the artifact.

### `discovery/compiler.py`

Deterministic (second LLM call is **not** used):

- Turn trace events into `Step`s
- Replace the member id from the goal (`12345`) with `{{params.memberId}}`
- Drop login fills
- Attach **vendor-level** outcomes/recoverables/hard failures even if this run only saw the happy path (so replay can still handle `99999`)
- Default extract: table cell “Savings balance”
- Default checkpoint: heading “Member detail”

If the model’s trace is messy, compiler falls back to the same two default lookup steps we hand-wrote in `capabilities/`.

---

## 10. Policy and redaction

**Files:** `policy/core-console.yaml`, `bankgpt/policy/guard.py`

YAML says: only `localhost` / `127.0.0.1`, allowed actions, denied download/upload/eval, irreversible **names** (Close account, Post transaction, Wire), max 25 discovery steps.

`PolicyPack.host_allowed` / `action_allowed` are called on every navigate and act in **both** runners. The model cannot “just this once” go to another host.

`redact_text` / `redact_obj` mask PAN-like digit runs, SSNs, and keys named password/token/…. Evidence JSONL goes through `redact_obj`.

Limits (honest): this is regex DLP, not a bank-grade scrubber; screenshots can still show PII.

---

## 11. Evidence

**File:** `bankgpt/evidence.py`

Each run gets `evidence/<runId>/`:

- `run.jsonl` — append-only events (navigate, act, checkpoint, outcome, fail)
- `result.json` — final `RunResult`
- `artifact.json` — copy of the capability
- `failure.png` / `failure.a11y.json` on fail
- `intervention.json` (+ png) on escalate
- `trace.zip` — Playwright trace
- `control.json` — operator signal (when used)

Committed demos: `evidence/replay-success`, `replay-not-found`, `replay-escalated`. Test runs `evidence/test-*` are gitignored.

---

## 12. Tests

**Files:** `tests/conftest.py`, `test_replay.py`, `test_schema.py`

`conftest` starts Core Console on **port 3010** (so it does not fight a demo on 3000).

Tests (no API key):

- Replay `12345` → success, balance `1240.50`
- Replay `99999` → `business_outcome` / `MEMBER_NOT_FOUND`
- Close-account capability → `escalated`
- Schema loads
- PAN redaction

These prove the production path without OpenAI.

---

## 13. Walk one replay in slow motion

Command:

```bash
python -m bankgpt replay --capability lookup-member-savings --param memberId=12345
```

1. `cli._replay` loads JSON → `Capability` model.
2. Session starts headless Chromium.
3. Runner goes to `http://127.0.0.1:3000/login`.
4. Fills teller/demo, Sign on → `/search` (iframe with Member ID).
5. Step `fill_member_id`: find textbox in **iframe**, type `12345`.
6. Step `submit_search`: click Search → `/member/12345`.
7. Recoverable: System notice dialog → click OK.
8. Outcome matchers: “No record found”? No.
9. Checkpoint: heading “Member detail”? Yes.
10. Extract table cell Savings balance → `"1240.50"` after stripping `$` and commas.
11. `status: success`. Browser closes. `result.json` written.

Same command with `99999`:

5–6. Type 99999, Search → `/not-found`.  
8. Outcome matches → **return here**. Checkpoint never required. `status: business_outcome`. This is the “not a crash” design.

---

## 14. Walk discovery (when you have a key)

```bash
python -m bankgpt discover --goal "Look up member 12345 and read the current savings balance"
```

1. Same login bootstrap.
2. Model sees a11y of search iframe.
3. Calls `act(fill Member ID, 12345)`, then `act(click Search)`, maybe `act(click OK)` on the dialog, then `done` with a balance.
4. Compiler writes `capabilities/lookup-member-savings.json`.
5. You then replay **without** the model.

Until you run this, the JSON in `capabilities/` is **hand-authored** to the same schema the compiler would emit. That is valid for replay demos; graders still want one real discover folder in `evidence/`.

---

## 15. HITL in the code (not a separate service)

There is no `escalation/` package. It is methods on the runners + one CLI command.

1. `EscalationNeeded` or discovery `stuck`.
2. `session.set_owner("human")`.
3. Write `intervention.json`.
4. Optional wait on `control.json`.
5. `python -m bankgpt operator resume --run <id>` writes that file.
6. Owner back to `automation`.

The “operator UI” is the headed Chromium window plus that CLI. Intentional mock; the lock is real.

---

## 16. What each file is *not* responsible for

| File | Does not do |
|---|---|
| `core_console` | Any LLM, any capability |
| `replay/runner.py` | Call OpenAI |
| `discovery/llm.py` | Click the browser (runner + adapter do) |
| `cli.py` | Locator strategy |
| `schema.py` | Talk to Playwright |
| `session.py` | Decide next step |

If you add a desktop app later, you add `surface/desktop.py` and point `Session` at it. You should not need a new capability format.

---

## 17. Suggested study order (one sitting)

1. Open `capabilities/lookup-member-savings.json` and `core_console/templates/search_frame.html` + `detail.html` + `not_found.html`. Match JSON locators to HTML.
2. Read `cli.py` `_replay` then `ReplayRunner.run` top to bottom.
3. Read `WebA11yAdapter.find` and `snapshot`.
4. Read `DiscoveryRunner.run` and `TOOLS` in `llm.py`.
5. Read `Session.owner` + `operator` in `cli.py`.
6. Skim `REPORT.md` — that is what you must be able to defend in an interview.

That is the whole codebase: a fake core, a typed skill file, a dumb executor, a one-time teacher, a lock on one browser, and logs to prove it happened.
