# BankGPT — Design (in depth)

This document is the design-of-record for the Interface.ai take-home: a computer-use system that lets an AI agent operate legacy bank UIs that have **no API**.

The product thesis, in one line:

> The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the AI agent invokes it in production.

This is not a chatbot, not a banking API integration, and not a scaled RPA platform. It is a complete vertical slice of that thesis: goal → live LLM-driven run → versioned capability → LLM-free replay with a real error taxonomy → human takeover of the **same** live session → evidence.

---

## 1. What they are actually asking us to build

Interface.ai’s agents decide *what* work to do for a bank. This system is *how* that work gets done when the only door into the institution’s software is the UI a human operator would use.

Two integration paths exist in the real product. Only one is in scope here.

| Path | When | This project |
|---|---|---|
| API integration | The core/servicing system exposes a real API | **Out of scope.** If an API exists, you call the API. |
| Computer use | The app is a screen: core console, servicing tool, admin UI, desktop | **In scope.** Drive the UI. Record. Replay. |

The sentence in the brief — *“when a system exposes an API, we integrate through the API — that’s always the preferred path and is out of scope”* — is not asking us to design a bank API. It is telling us **not to pretend the hard problem is HTTP**. The hard problem is: stable but messy enterprise UIs, no test IDs, runtime business errors, and a need to turn a one-time model run into a cheap, reviewable, parameterized skill.

The system we are building therefore has two runtimes sharing one session and one artifact schema:

1. **Discovery** — an LLM observe → decide → act loop against a live surface. Expensive, non-deterministic, used rarely (record-once).
2. **Replay** — a deterministic executor that walks a saved capability with caller-supplied parameters. Cheap, auditable, used in production (replay-many).

A third path exists when either runtime cannot safely continue:

3. **Escalation** — pause automation, give a human the **same live session**, capture what they did, resume or abort.

---

## 2. Constraints that drive every decision

The brief’s “real environment” is not flavor text. Three properties decide the architecture.

### 2.1 Stable UIs, messy runtime

Back-office bank apps change slowly. Record-once / replay-many is viable **because of that**, not in spite of it. The interesting failures are not “the CSS class renamed this sprint.” They are legitimate runtime states:

- validation errors
- record not found
- permission denials
- unexpected confirmation / interstitial dialogs
- session / idle timeout
- transient slowness
- outright application errors

A capability that only works on the happy path is not a capability. Replay must **classify** what happened, not just throw.

The most common design mistake the brief calls out: treating “no such member” as a crash. It is a **business outcome** the calling agent needs to know.

### 2.2 Heterogeneous, often legacy surfaces

A given app might be a modern SPA, a server-rendered 2004 console (framesets, nested tables, no semantic markup, no test IDs), or a native desktop app. We cannot assume a clean DOM or stable CSS selectors.

We will **implement** one surface (a deliberately hostile local web console). We will **design** perception/action behind a seam so the recorded flow is not glued to “Playwright + CSS.”

### 2.3 Multi-tenant at scale (design, not build)

Hundreds of institutions, ~20 apps each. Many tenants run the **same vendor product**, branded and versioned differently. An artifact should be about the vendor app, with room for per-tenant overlays — not “re-record for every bank.”

We will not build tenant plumbing. The schema and identity model must not paint us into a per-tenant-script corner.

### 2.4 What we are graded on (so we go deep here, not elsewhere)

In order: system design (especially artifact schema and replay contract) → a working discovery+replay loop → error taxonomy and locators → real HITL control transfer → a credible heterogeneity/multi-tenant story → safety → readable code → the write-up.

They do **not** reward queues, clusters, Temporal-as-flex, or feature breadth. Depth on the load-bearing pieces; a thin-but-real version of every core requirement.

---

## 3. Design principles

1. **Capability, not transcript.** The saved artifact is a typed, versioned, reviewable contract an agent can invoke. The raw model conversation is evidence, not the product.
2. **Parameters in, PII out.** Concrete values from the discovery run (member `12345`, balances, names) are either parameters, extracted outputs, or redacted. They are not baked into steps as literals that would leak into git.
3. **Locator rank, not a single selector.** Each target is a list of strategies, most human-stable first (accessible name / role), CSS last if at all.
4. **Outcomes are first-class.** The artifact declares how to recognize business results and recoverables. The replay result type distinguishes success / business outcome / recoverable-handled / hard failure.
5. **One live session, one owner.** At any moment exactly one controller owns the browser: `automation` or `human`. Handoff is a lock transfer, not a new window.
6. **Policy is not a prompt.** Allowlists and risk classes are enforced in code on every action, in both discovery and replay.
7. **Design for the second surface; implement the first.** The adapter is the only place that knows “this is a web page.”
8. **No infra until the contract is real.** In-process loop now. Production orchestration (Temporal, queues) can wrap a session manager later; it cannot replace one.

---

## 4. Target application — local Core Console

We will **not** automate a real bank, and we will **not** depend on a public site’s ToS, uptime, or inability to inject “member not found.”

We will build a small local app: **Core Console**, a stand-in for a legacy core-banking screen. It exists so we can exercise the problems the brief actually cares about.

### 4.1 Why a local app

| Requirement | Public demo site | Local Core Console |
|---|---|---|
| Multi-step flow | Maybe | Yes, we own it |
| No clean DOM / no test IDs | Sometimes | Intentional: tables, iframe, no test IDs |
| Business outcome (not found) | Hard to guarantee | Deterministic: member `99999` |
| Recoverable interstitial | Unreliable | A dismissible “system notice” |
| Session timeout / permission | Usually absent | We can trigger them |
| Credentials / PII | Dangerous | Fake only |
| Cross-tenant story | N/A | Optional second skin of the same app |

### 4.2 What the console contains

Two operator flows, plus fixtures for exceptional states.

**Flow A — Member lookup (the primary demo capability, `risk: read`)**

1. Login (demo credentials; never persisted into artifacts).
2. Member search by ID.
3. Member detail: name (masked in logs), savings balance, status.
4. Explicit empty state: “No record found.”

**Flow B — Open sub-account (write path, `risk: write`)**

1. From member detail, open a sub-account form (type, nickname).
2. Confirmation screen (“review and submit”).
3. Success confirmation number.

**Exceptional states (replay must classify these)**

| Trigger | What the UI shows | Class |
|---|---|---|
| Member ID `99999` | “No record found” | Business outcome `MEMBER_NOT_FOUND` |
| Empty required field | Inline validation | Business outcome `VALIDATION_ERROR` |
| Role `teller` hitting an admin-only action | “You do not have access” | Business outcome `PERMISSION_DENIED` |
| First search of a session (or query flag) | Modal “System notice — scheduled maintenance” | Recoverable: dismiss, continue |
| Idle / `?timeout=1` | Session expired overlay | Hard failure `SESSION_EXPIRED` |
| Artificial delay | Spinner / slow table | Recoverable: wait/retry |
| “Close account” | Irreversible confirm | Policy: always escalate |

**Hostility (legacy texture)**

- No `data-testid`.
- Member results as nested `<table>` markup, not a nice card component.
- One iframe around the search form (frames / nested browsing contexts are common in cores).
- Labels that are visually present but not always `for=`-linked — so we prefer role + accessible name, not `#id`.

**Optional later:** a second tenant skin (`/t/northstar/...`) with the same structure, different heading copy and a renamed button. Used only to *talk about* overlays, not as required infra.

### 4.3 Why this is a fair proxy

A real core is bigger. The *shape* is the same: search → detail → action → confirmation, plus empty states and dialogs. That is enough to prove discovery, parameterization, replay, outcomes, and HITL.

---

## 5. Architecture

One Python process. One live browser session. File-backed artifacts and evidence. No Temporal, no queue, no database.

```
                    ┌──────────────────────────────────────┐
                    │  CLI                                  │
                    │  discover / replay / operator         │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────▼───────────────────┐
                    │  Orchestrator                         │
                    │  DiscoveryRunner | ReplayRunner       │
                    │  EscalationController                 │
                    └─┬──────────────┬──────────────┬──────┘
                      │              │              │
           ┌──────────▼──┐   ┌───────▼──────┐  ┌────▼─────────┐
           │ Policy      │   │ Artifact     │  │ Evidence     │
           │ allowlist,  │   │ store (JSON) │  │ JSONL +      │
           │ risk,       │   │              │  │ screenshots  │
           │ redaction   │   └──────────────┘  └──────────────┘
           └──────────┬──┘
                      │ every action
           ┌──────────▼────────────────────────────────────┐
           │  Session                                       │
           │  owner: automation | human                     │
           │  live Playwright browser (headed for HITL)     │
           └──────────┬────────────────────────────────────┘
                      │
           ┌──────────▼────────────────────────────────────┐
           │  SurfaceAdapter (interface)                    │
           │  snapshot() / act() / extract()                │
           │                                                │
           │  WebA11yAdapter   (implemented)                │
           │  DesktopAdapter   (designed, not built)        │
           └───────────────────────────────────────────────┘
```

### 5.1 Boundaries

| Component | Responsibility | Not responsible for |
|---|---|---|
| CLI | Parse goal/params, print structured results | LLM prompts, locators |
| DiscoveryRunner | LLM loop, emit artifact from a successful trace | Replay semantics |
| ReplayRunner | Execute artifact, classify outcomes | Calling the model |
| EscalationController | Pause, write intervention, wait for resume/abort | Pretty operator UX |
| Session | Lifetime of one browser, control lock, traces | Policy |
| SurfaceAdapter | Perceive and act in surface-native terms, mapped to a **shared action/locator vocabulary** | Capability schema |
| Policy | Allow host/action, risk class, redact | Finding elements |
| Artifact store | Read/write versioned JSON | Execution |
| Evidence | Append-only run log + failure artifacts | Decision making |

### 5.2 Why a single process

The load-bearing difficulty is the contract (schema, locators, outcomes, control lock), not distribution. A live browser is a sticky resource; putting a network hop between orchestrator and browser would force us to solve session affinity before we have a working loop.

Production evolution (called out, not built): a **session manager** process holds browsers; a workflow engine (Temporal) orchestrates discovery/replay/HITL *around* that manager. Temporal does not hold the browser.

### 5.3 Control flow of a run

```
Run starts
  → Session created (owner = automation)
  → Policy binds allowlist to this run
  → if mode = discover: LLM loop until Done | Stuck | MaxSteps
  → if mode = replay: walk steps until checkpoint | outcome | failure
  → on Stuck / irreversible / unknown dialog:
        owner = human
        write InterventionRequest
        wait for operator command (resume | abort)
        record human segment
        owner = automation
        continue or complete
  → persist evidence, return RunResult
  → Session closed (unless operator still attached)
```

---

## 6. Surface abstraction

This is the seam that keeps us from painting into a “web-only CSS bot” corner.

### 6.1 Shared vocabulary (surface-agnostic)

The artifact and both runners speak this language:

**Actions:** `click`, `fill`, `select`, `press`, `dismiss`, `wait`, `extract`, `navigate` (navigate is policy-gated).

**Locator kinds (ranked):**

| Rank | Kind | Meaning | Web mapping | Desktop mapping (future) |
|---|---|---|---|---|
| 1 | `role` | AX role + accessible name | `getByRole` | UI Automation / AX role + name |
| 2 | `label` | Visible label text | label / aria-label | Nearby label |
| 3 | `placeholder` | Placeholder | placeholder | n/a or AX description |
| 4 | `text` | Visible text / heading | getByText | Name/value |
| 5 | `table_cell` | Header + row key | table role | DataGrid cell |
| 6 | `nth` | Position among matches | nth | Index (last resort) |

CSS / XPath are **not** stored as the primary locator. If we keep a CSS fallback at all, it is last, flagged `fragile: true`, and never the only locator.

**Snapshots** the adapter must produce:

- A compact **accessibility tree** (roles, names, values, states: disabled, expanded, dialog).
- A screenshot (always on failure / escalation; optionally each step in discovery).
- Surface metadata: URL or window title, dialog present, busy/loading.

The LLM sees the a11y snapshot (and a screenshot if the tree is insufficient). Replay uses locators, not the snapshot, except to **detect** outcomes and recoverables.

### 6.2 Adapter interface (conceptual)

```
snapshot() -> SurfaceSnapshot
act(action, target?, value?) -> ActResult
extract(target) -> str
find(target) -> Handle | Miss
```

`WebA11yAdapter` implements this with Playwright. A future `DesktopAdapter` (pywinauto / macOS AX) implements the same methods. **The artifact does not change kind.** A desktop recording would use the same `role` locators; only the adapter behind the session changes.

### 6.3 Why accessibility tree, not screenshot-click, not raw DOM

| Approach | Discovery | Replay | Desktop | Verdict |
|---|---|---|---|---|
| CSS / XPath | Easy on clean DOM | Brittle on legacy | No | Reject as primary |
| Screenshot + coordinates | Very general | Flaky (theme, DPI, animation) | Yes | Discovery aid only |
| Accessibility tree | Close to what an operator/screen reader perceives | Stable names/roles on enterprise apps | Yes | **Primary** |

Screenshot+coordinates is “more computer-use” in the marketing sense and worse as a **production locator**. We may attach a screenshot to the LLM on discovery when the tree is sparse. We do not serialize click coordinates into the capability.

---

## 7. Artifact schema

The artifact is the product. It is what a reviewer audits and what a calling agent invokes.

### 7.1 Identity and versioning

```yaml
apiVersion: bankgpt/v1
kind: Capability
metadata:
  id: lookup-member-savings          # stable callable name
  version: 1                         # integer; bump on breaking step/IO change
  vendorApp: core-console            # the product, not the tenant
  recordedOn:
    tenant: demo                     # provenance only
    appVersion: "1.0"
    at: "2026-08-14T00:00:00Z"
  description: >
    Look up a member by ID and return the current savings balance.
```

- `id` + `version` is the invocation key.
- `vendorApp` is the reuse key across institutions.
- `recordedOn.tenant` is provenance, not identity. Tenant overlays (unbuilt) would be `vendorApp + tenant` specializations, not new unrelated scripts.

### 7.2 Agent-facing contract (the function signature)

```yaml
spec:
  parameters:
    - name: memberId
      type: string
      required: true
      sensitive: false
      description: Member number as shown on the search screen
  outputs:
    - name: savingsBalance
      type: money
      description: Current savings balance as displayed
    - name: memberStatus
      type: string
  policy:
    risk: read                       # read | write | irreversible
    allowedHosts: ["127.0.0.1", "localhost"]
    allowedActions: ["click", "fill", "extract", "dismiss", "wait"]
```

This is deliberately shaped like a tool definition: name, typed args, typed returns, risk. A later stretch is to expose the catalog as LLM tools. The schema is already that shape so we do not have to rewrite it.

Sensitive parameters (`password`, full SSN) may be *supplied* at runtime from a secret store / env. They are never written into the artifact or into evidence without redaction. Discovery must replace observed literals with `{{params.*}}` when the value was an input.

### 7.3 Steps

Each step is a single act plus optional local wait/checkpoint.

```yaml
  steps:
    - id: fill_member_id
      action: fill
      target:
        locators:
          - { by: role, role: textbox, name: "Member ID" }
          - { by: label, text: "Member ID" }
      value: "{{params.memberId}}"
    - id: submit_search
      action: click
      target:
        locators:
          - { by: role, role: button, name: "Search" }
          - { by: text, text: "Search" }
      wait: { state: idle, timeoutMs: 10000 }
```

`id` is stable so failure reports and HITL context can say “stuck at `submit_search`,” not “step 4.”

`value` supports only:

- `{{params.<name>}}`
- literal constants that are **not** PII (e.g. selecting product type `"SAVINGS"` if that is part of the capability, not the caller)

No `{{env.PASSWORD}}` in the file if we can avoid it; credentials are session-level, injected by the runner from env, and redacted.

### 7.4 Checkpoints

A checkpoint is an assertion that we reached the intended screen, not that a click returned 200.

```yaml
  checkpoints:
    - id: on_member_detail
      after: submit_search
      assert:
        locators:
          - { by: role, role: heading, name: "Member detail" }
```

Replay does not assume success because the action did not throw. It verifies the checkpoint (or an outcome matcher) before continuing.

### 7.5 Outcomes vs recoverables vs failures

Declared in the artifact so replay is not guessing from screenshots with a model.

```yaml
  outcomes:
    - code: MEMBER_NOT_FOUND
      kind: business_outcome
      detect:
        locators:
          - { by: role, role: status, name: "No record found" }
          - { by: text, text: "No record found" }
      after: submit_search

    - code: PERMISSION_DENIED
      kind: business_outcome
      detect:
        locators:
          - { by: text, text: "You do not have access" }

  recoverables:
    - id: system_notice
      detect:
        locators:
          - { by: role, role: dialog, name: "System notice" }
      action: dismiss
      maxTimes: 2

    - id: busy
      detect: { busy: true }
      action: wait_retry
      timeoutMs: 15000

  hardFailures:
    - code: SESSION_EXPIRED
      detect:
        locators:
          - { by: text, text: "Session expired" }
      escalate: true
```

**Evaluation rule we are encoding:** if a matcher in `outcomes` hits, the run **succeeds as a known result**. The caller gets `status: business_outcome`. That is not `status: failed`.

### 7.6 Extraction

```yaml
  extract:
    - output: savingsBalance
      from:
        locators:
          - { by: table_cell, column: "Savings balance" }
          - { by: role, role: cell, name: "Savings balance" }
      transform: money            # strip $ and commas; still store display form in evidence redacted
```

Extraction runs only after the success checkpoint, and only if no business-outcome matcher fired.

### 7.7 What is deliberately *not* in the artifact

- Raw LLM messages
- Screenshots (those live in `/evidence/<runId>/`)
- Cookies, storage state, passwords
- Pixel coordinates
- Tenant branding copy except as a locator **fallback**
- A general-purpose script/DSL (no loops, no JS). Linear steps + declared matchers. If a flow needs a real branch beyond outcomes, that is either two capabilities or a later compiler. Keep v1 linear so it is reviewable.

### 7.8 Result contract (what replay returns)

```json
{
  "status": "success | business_outcome | failed | escalated",
  "capability": "lookup-member-savings",
  "version": 1,
  "outcomeCode": null,
  "outputs": { "savingsBalance": "1240.50", "memberStatus": "Active" },
  "error": null,
  "debug": {
    "runId": "...",
    "failedStep": null,
    "expected": null,
    "observed": null,
    "evidenceDir": "evidence/..."
  }
}
```

On failure, `debug` is required: step id, expected locator/checkpoint, observed snapshot summary, screenshot path.

---

## 8. Discovery — the LLM in the loop

### 8.1 Inputs

- Natural-language **goal**
- **Target** entry URL
- Policy pack (hosts, actions, risk)
- Optional hints (“you are a teller; do not close accounts”)

### 8.2 Loop

Until `done` | `stuck` | `max_steps` | `timeout`:

1. `snapshot()` → compact a11y tree (+ screenshot if last action failed or tree is huge/empty).
2. Redact snapshot text (mask anything that looks like PAN/SSN/password).
3. LLM called with: goal, policy, last N steps, current snapshot.
4. Model returns **one structured tool call**, not free-form computer use:
   - `act(click|fill|...)`
   - `extract(...)`
   - `done(outputs, rationale)`
   - `stuck(reason)`
5. Policy checks the action (host after any navigation, action type, risk class).
6. Adapter executes. Evidence logger appends `{step, action, target, rationale, result}` with values redacted.
7. If `done`: compiler turns the **redacted action trace + locators the adapter actually used** into a Capability JSON. Human-reviewable. Version 1, status implicitly `draft` (approval workflow is a stretch).

### 8.3 Prompt / model stance

- Provider: OpenAI or Anthropic via env; the runner depends on a `LLMClient` interface so the rest of the system does not.
- Tools, not a giant “computer use” pixel agent. The model is picking from our action vocabulary against an a11y observation. That keeps the emitted artifact in our schema instead of a proprietary CUA trace.
- The model is told: parameterize IDs; never echo passwords; call `stuck` rather than guess on irreversible actions; prefer labeled controls.

### 8.4 Compilation from trace → artifact

This is a deterministic compiler, not a second LLM call (an LLM *may* propose parameter names; we still validate).

- Every `fill` whose value equals a goal-extracted slot (e.g. `12345`) becomes `{{params.memberId}}`.
- Locators stored are the **ranked set the adapter resolved**, not a single lucky CSS path.
- `done.outputs` plus extract calls become `spec.outputs` / `extract`.
- After a search click, if the model proceeded to a heading “Member detail”, that becomes a checkpoint.
- We seed `outcomes` from known Core Console matchers **and** from any screen the model treated as terminal-but-successful (“no record found” if the goal was a lookup that can legally miss). For a first slice, Core Console’s outcome library can be merged in by `vendorApp` so the artifact is complete even if the discovery run only saw the happy path.

Seeding vendor-level outcome matchers is intentional: production replay must handle not-found even if the recording used a live member. That is how record-once works in a bank: you cannot record every exception, but you can declare how exceptions look for this vendor screen.

### 8.5 Stopping conditions

- `done` with checkpoint matched
- `stuck` → escalation
- `max_steps` (e.g. 25) → escalation
- Policy violation → hard fail (do not escalate into “please do the illegal thing”)
- Wall timeout

Discovery **must** be a real model run. Evidence of that run is a submission requirement.

---

## 9. Deterministic replay

No LLM in the decision loop. The executor is a state machine over the artifact.

### 9.1 Algorithm

```
bind params (type-check, required)
open session at capability entry (or current URL if continuing)
for step in steps:
    handle recoverables (loop: detect → dismiss/wait, bounded)
    if hardFailure matcher → fail or escalate
    if outcome matcher for this `after` → return business_outcome
    resolve target via ranked locators (wait until visible/enabled or timeout)
    if miss → fail with expected vs observed
    policy.check(action)
    act
    if checkpoint.after == step.id → assert checkpoint
return extract(outputs) if all checkpoints passed
```

### 9.2 Locator resolution

For each locator in order: try, wait up to `timeoutMs` (default 8s) for actionable state. First hit wins. If all miss: hard failure, screenshot, a11y dump of the current dialog/page.

We wait for **actionability** (visible, enabled, stable), not merely attached. Legacy cores paint tables slowly.

### 9.3 Error taxonomy (the replay contract)

| Class | How detected | Executor behavior | Result `status` |
|---|---|---|---|
| Success | Checkpoints pass, extracts succeed | Return outputs | `success` |
| Business outcome | Artifact `outcomes[]` matcher | Stop, no exception | `business_outcome` + code |
| Recoverable | `recoverables[]` matcher | Handle, continue, count toward `maxTimes` | (transparent unless it exceeds bound) |
| Hard failure | Timeout, locator miss, unhandled dialog, app error page, policy | Stop, debug payload | `failed` |
| Escalation | `escalate: true` matcher, irreversible step, or repeated recoverable | HITL | `escalated` then later success/fail |

Unhandled unexpected dialog: **do not click OK blindly**. That is how you confirm a wire. Treat as stuck → escalate.

### 9.4 Determinism, honestly

Replay is deterministic **in decisions**, not in wall-clock or in the bank’s data. Same artifact + same params + same app state ⇒ same steps and same result class. If the member’s balance changed, the output value changes; the capability still succeeded. If the member was deleted, we should hit `MEMBER_NOT_FOUND`, which is still a determined branch, not a random model choice.

Secondary: UI drift. Ranked a11y locators absorb copy-minor changes worse than CSS, better than coordinates. If the vendor renames “Search” to “Find member”, replay fails loudly at `submit_search` with expected/observed — then a human updates the locator list or re-records. We do not silently LLM-patch production steps in v1 (assisted fallback is a stretch).

---

## 10. Human-in-the-loop — control transfer

A full co-browsing console is out of scope. The **mechanism** is in scope and must be real.

### 10.1 When we escalate

- Discovery: `stuck`, max steps, low-confidence irreversible intent
- Replay: unknown dialog, locator miss if configured to escalate rather than fail, `hardFailures[].escalate`, policy `risk: irreversible`
- Operator can also attach preemptively (design; optional)

### 10.2 Control lock

`Session.owner ∈ {automation, human}`.

- Automation may call `act` only if `owner == automation`.
- When escalating: freeze the runner (do not close the browser), set `owner = human`, write `InterventionRequest`.
- Human uses the **same headed Playwright window**.
- Resume: operator command sets `owner = automation` and either `continue` (replay retries current step / discovery snapshots again) or `complete` (treat human’s navigation as success and optionally capture outputs).
- Abort: `status: failed` with reason `operator_abort`.

Who is in control is visible in the run log at all times. That is the seam the brief asks for.

### 10.3 Intervention request (context to act)

Written to `evidence/<runId>/intervention.json` and printed to the CLI:

- capability / goal
- run id, mode (discover|replay)
- current step id
- why (`unknown_dialog`, `locator_miss`, `irreversible`, `max_steps`)
- screenshot path
- snapshot excerpt (redacted)
- last N actions

### 10.4 Operator surface (intentionally minimal)

Not a product UI. A CLI:

```
bankgpt operator wait --run <id>     # blocks until intervention (or used by the runner itself)
bankgpt operator resume --run <id>
bankgpt operator abort --run <id>
```

The runner, when paused, waits on a file or local socket (`evidence/<runId>/control.json` → `{ "command": "resume" }`). That is a real resume signal. A one-page local HTML “Resume / Abort” button that writes the same file is optional sugar.

Playwright headed mode **is** the co-browse. The human’s mouse in that window *is* taking control of the live session. We do not CDP-mirror to a second browser (out of scope).

### 10.5 Recording the human segment

Playwright tracing stays on across the pause. On resume we export the trace segment, log `actor: human`, and (best effort) note URL changed. We do not try to reverse-compile human clicks into new artifact steps in v1 — we record that a human intervened so the capability can be marked dirty / needs review. Re-record is the honest path after a structural UI change.

### 10.6 Why this is enough

The brief: pause, expose the live session, signal resume, capture that a human acted, plus a clear design for the rest. We have a real lock, a real wait, the same OS window, and an intervention payload. The rest (routing to a staffing queue, real-time cursor sharing, RBAC on operators) is Cuts.

---

## 11. Safety

### 11.1 Allowlist (enforced in code)

Configurable YAML, e.g. `policy/core-console.yaml`:

- `allowedHosts`: `localhost`, `127.0.0.1`
- `allowedUrlPatterns`: `^https?://(localhost|127\.0\.0\.1)(:\d+)?/`
- `allowedActions`: click, fill, select, press, dismiss, wait, extract
- `deniedActions`: file upload, download, OS-level, eval/JS, new tab to unknown host

Every `act` and every navigation is checked. Discovery cannot “just this once” leave the host. Replay cannot follow a link the artifact grew by accident.

### 11.2 Risk classes

| Class | Examples | Default handling |
|---|---|---|
| `read` | lookup, read balance | Auto replay |
| `write` | open sub-account | Allowed in demo; logged as write; optional confirm flag |
| `irreversible` | close account, post/submit money movement | **Never** auto-execute in v1; escalate |

The capability’s `policy.risk` is the max of its steps. A read capability must not contain a submit-on-close-account step.

### 11.3 Redaction

Never persist into artifacts, JSONL, or snapshots written to disk:

- passwords, tokens, cookies
- PAN-like 13–19 digit sequences
- SSN-like patterns
- full account numbers

Replacement: `[REDACTED:<type>]`. Member IDs used as **parameters** are not secrets in this demo (they are the lookup key); we still do not dump unrelated PII from the detail page into the artifact. Evidence may keep a redacted screenshot; we accept that pixels can leak and document that production would need screenshot scrubbing / restricted evidence buckets.

### 11.4 Limits (honest)

- Allowlist is host/action, not a full semantic policy (“never move more than $X”).
- Headed HITL means a human can do anything in that window; we record, we do not sandbox the operator.
- Redaction is pattern-based, not a DLP product.
- The model could try to type a secret into a locator name; we redact traces, we cannot redact the live bank screen.

---

## 12. Heterogeneity and multi-tenant (design only)

### 12.1 Second surface (desktop)

The artifact already stores role/name locators and a linear act list. A desktop adapter implements `snapshot/act/find` with OS accessibility. ReplayRunner does not branch on “web vs desktop”; Session is constructed with an adapter for that `vendorApp`’s surface kind.

What would need to grow later: window targeting (app title, process), and maybe `press` key chords. Not a different capability format.

### 12.2 Legacy web vs modern web

Both use `WebA11yAdapter`. Frames: adapter searches the frame tree when resolving locators (Core Console’s iframe exists to force this). Nested tables: `table_cell` locator kind. No test IDs required.

### 12.3 Cross-tenant reuse

Identity:

```
Capability identity     = vendorApp + capability id + version
Tenant overlay identity = tenantId + capability id + version
```

Base artifact describes the vendor screen. Overlay (unbuilt) would be a sparse JSON:

- locator replacements (“Search” → “Find member”)
- extra recoverable (tenant-specific interstitial)
- host allowlist addition (that tenant’s VPN hostname)
- not a fork of all steps

Drift detection (unbuilt): replay telemetry — locator used (primary vs fallback), checkpoint fail rate per tenant. If fallback rate spikes, flag overlay needed. Do not auto-LLM-rewrite production artifacts.

We may optionally ship two Core Console skins to make this concrete in the write-up without building overlay merge.

---

## 13. Why Temporal is not in this system

Temporal is a reasonable **production** thought: durable wait for an operator signal, retries, timeouts, an audit trail of workflow history.

It is the wrong core for this take-home, for reasons that belong in the design (not just “scope”).

1. **The brief penalizes premature infra.** Queues, clusters, and orchestration frameworks are not what they are scoring.
2. **The browser is not a Temporal activity.** Activities are short and retryable on any worker. A headed Playwright session is long-lived and sticky. If the workflow resumes on another process, the page is gone. You still need a session manager *outside* Temporal. HITL’s hard part is keeping the live session, which Temporal does not do.
3. **Our pause is a lock on that session.** A file/socket wait in-process is an honest model of `WaitForSignal`. Mapping it to Temporal later is a wrapping exercise, not a rewrite of locators or schema.

What we *do* take from the Temporal shape: run as an explicit state machine; HITL as a typed wait; activities (act, snapshot) as bounded calls with timeouts. That mapping is documented so the abstraction is not a dead end.

---

## 14. Observability and `/evidence`

Each run gets `evidence/<runId>/`:

| File | Contents |
|---|---|
| `run.jsonl` | Append-only events: snapshot (redacted summary), decision/action, policy, owner change |
| `artifact.json` | Copy of capability if produced or used |
| `result.json` | Final RunResult |
| `failure.png` | On fail/escalate |
| `failure.a11y.json` | Compact tree on fail |
| `intervention.json` | If escalated |
| `trace.zip` | Playwright trace (optional, gitignored if huge; keep a small one for the submission run) |

Submission will include:

- one discovery log + the emitted artifact
- one successful replay
- one replay that hits `MEMBER_NOT_FOUND` (or injected failure)
- ideally one escalation folder

Raw model payloads: stored redacted, or truncated. Never secrets.

---

## 15. Stack and repository layout

| Choice | Decision |
|---|---|
| Language | Python 3.12 |
| Browser | Playwright (headed for demo/HITL, headless for CI replay) |
| LLM | Tool-calling client behind an interface; API key from env |
| Artifact | JSON files under `capabilities/` |
| Policy | YAML under `policy/` |
| Local app | Core Console (small web app in-repo) |
| Tests | Replay engine + outcome classification against Core Console **without** LLM; schema validation |
| Not used | Temporal, Redis, Postgres, Kubernetes, Puppeteer, raw Selenium |

```
/
  README.md                 # setup + demo commands
  REPORT.md                 # 7 required headings, short
  DESIGN.md                 # this document
  capabilities/             # saved artifacts
  policy/
  evidence/                 # committed demo runs
  core_console/             # local target app
  bankgpt/
    cli.py
    orchestrator/
    session/
    surface/                # adapter protocol + web_a11y
    artifact/               # schema, load/save, compiler
    replay/
    discovery/
    policy/
    escalation/
    evidence/
  tests/
```

CLI demo path (exact commands will land in README):

```
# terminal 1
python -m core_console

# terminal 2
python -m bankgpt discover --goal "Look up member 12345 and read the savings balance" \
  --target http://127.0.0.1:3000
python -m bankgpt replay --capability lookup-member-savings --param memberId=12345
python -m bankgpt replay --capability lookup-member-savings --param memberId=99999
```

HITL demo: a flag or a replay against a forced unknown dialog, then `operator resume`.


