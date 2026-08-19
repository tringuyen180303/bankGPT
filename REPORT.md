# REPORT

## 1. Architecture

BankGPT is one Python process: a CLI, an in-process orchestrator, one live Playwright **session**, and a **surface adapter**. Discovery and replay share session, policy, artifact schema, and evidence. They do not share a decision engine.

Discovery is an LLM observe → tool-call → act loop. Replay is a state machine over JSON. Escalation sets `Session.owner` from `automation` to `human` without closing the browser.

The load-bearing seam is `SurfaceAdapter` (`snapshot` / `act` / `find`). Today that is `WebA11yAdapter` (accessibility names across frames, not CSS). Capability JSON does not mention Playwright. A desktop adapter could implement the same three methods later.


## 2. Artifact schema

A capability is `apiVersion: bankgpt/v1` JSON: `id` / `version` / `vendorApp`, typed parameters and outputs, policy, linear steps with **ranked locators**, checkpoints, declared **outcomes**, **recoverables**, **hardFailures**, and extract specs. Inputs bind as `{{params.memberId}}` so a discovery literal is not the production contract.

It is a tool an agent can call, not a model transcript. Transcripts live in evidence. Login is `credentials_from_env` on the runner, never steps in the file.

`vendorApp` is the reuse key across tenants; `recordedOn.tenant` is provenance only. Overlays are designed, not implemented. The compiler is imperfect (small models emit messy traces); reviewable JSON in `capabilities/` is the source of truth for replay.

## 3. Determinism & error handling

Replay does not call the LLM. It binds params, allowlist-checks every act, resolves locators in rank order (role+name → label → text) across frames, waits for actionability, then classifies:

- **success** — checkpoints pass, extracts return
- **business_outcome** — e.g. `MEMBER_NOT_FOUND` for `99999`, `NO_CREDIT_PRODUCT` for `11111` (caller-visible, not a crash)
- **recoverable** — “System notice” dismissed, bounded `max_times`
- **failed** — locator miss, checkpoint miss, `SESSION_EXPIRED`, policy denial; step + redacted a11y + screenshot
- **escalated** — irreversible names (`Close account`), stuck, or `escalate: true` matchers

UI drift is secondary: a renamed control fails at a named step rather than guessing. `--times N` is a flakiness signal on the same capability; payment/draw are not idempotent, so stability demos use lookup.

## 4. Heterogeneity & multi-tenant

Perception/action sit in the adapter; the artifact stores role/name locators and acts. The demo core is already hostile web (iframe search, tables, no test IDs). Desktop would be another adapter over OS accessibility, same schema.

Many institutions share a vendor product: record against `vendorApp`, specialize with sparse tenant overlays (locator replacements, extra recoverables, extra hosts). Drift shows up as fallback-locator rate and checkpoint failures, then a human updates the overlay or re-records. No per-tenant fork of the whole script, and no silent LLM rewrite in production.

## 5. Escalation & handoff

Stuck is: `stuck` tool, max steps, policy-irreversible control, unknown dialog, or declared escalate matchers. We write `intervention.json` (capability/goal, step, reason, screenshot, redacted snapshot), set `owner=human`, and wait on `evidence/<run>/control.json` when `--wait-operator` is set.

The live session is the headed Playwright window—not a new login in Chrome/Safari. A red in-page bar (**Resume automation** / **Abort**) writes the same control file; `bankgpt operator resume|abort --run <id>` is the CLI equivalent. Operator clicks are logged to `human_actions.json` (`actor: human`). On resume, replay skips the blocked irreversible step and continues remaining steps on that page. We do not reverse-compile operator clicks into new capability steps (re-record after a structural UI change). A full co-browse console is out of scope; the lock, overlay, and resume are real.

## 6. Safety

`policy/core-console.yaml` allowlists localhost and a small action set. Denied: download, upload, eval. Read flows auto-replay; names like **Close account** never auto-execute. Login is env-only. Evidence goes through `redact_obj`: PAN/SSN-shaped strings, password/token assignments, demo member names. Member IDs remain parameters. Limits: pattern DLP, not a bank product; screenshots and `trace.zip` can still show the live page; a human in the headed window sees everything on screen.

## 7. Cuts

Discovery today uses **Ollama `llama3.2:latest`**. It can call tools, but it often mangles locators (`by` as an object, Post payment as a button). A hardcoded **coach** plus `hints/core-console.md` keep the demo finishing. Replay never uses the model.

What I would build next, instead of growing that coach in Python: **per-screen markdown playbooks** (the same idea as the coach, but editable without a code change). Each file would be one Core Console screen — search, member detail, post payment — with exact control names and the one legal next act. Discovery would inject only the playbook for the current URL/heading. Operators at a new tenant would update markdown, not `_coach()`. A stronger tool-calling model would still be the default for first-time record; the playbooks are the cheap way to specialize.

Also later, in order: a session manager so HITL survives process restart; sparse **tenant overlays** on `vendorApp`; HTTP `catalog`/`invoke`; `draft → approved` gated by `--times N`; a better compiler (parameterize `amount`, drop duplicate clicks); a desktop `snapshot`/`act`/`find` adapter; screenshot scrubbing. I would not add queues or codegen before those.
