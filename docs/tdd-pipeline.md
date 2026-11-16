# TDD Pipeline for Coding Agents — Design Notes

*2026-11-10, from a design session (pi+qwen with shaz). First target: the stack-installer.*

## The problem

Current loop: give the agent a spec, refine it, agent codes, human dogfoods, maybe it works. No machine-checkable acceptance criteria, no separation between the code writer and the thing that verifies it.

## The pipeline: three roles, strictly separated

| Role | Does | Does NOT |
|------|------|----------|
| **Creator** | Writes the code. Usually Claude. | Never grades its own work. |
| **Translator** | Compiles the human's natural-language acceptance criterion into a deterministic test plan (YAML). This is where the brains are. | Does NOT read the source code under test — it reads the criterion and the *rendered* UI (snapshot/screenshot), plus knows the cluster surface (what to assert on: pods, secrets, logs). Reading source would make it test implementation details instead of user behavior. |
| **Runner** | Executes the YAML plan step by step. Records PASS/FAIL per step with actual output. Always runs teardown. | Does NOT interpret failures, retry, skip, or suggest fixes. It reports; the human (or the creator, on the next iteration) interprets. |

**Model pairing rule:** runner/translator come from a *different model family* than the creator. Claude creates → pi+qwen (or pi+deepseek) tests. The point is different blind spots, not just a different session.

## Core principle

**The UI is the input path. The backend state is the assertion.**

We do not screenshot-test pixels. The browser clicks the *real* buttons — so form validation, JS state, wiring, and error handling are all genuinely exercised. But pass/fail is judged on what *landed*: pods running, secrets present, logs clean.

Some steps are **informational, not gates** (e.g. "what actually deployed on the demo cluster") — marked as such in the plan.

## Test plan format (YAML)

First working draft: `~/src/git/stack-installer/tests/e2e/stack-installer-e2e.yaml` (v2).

```yaml
environment:
  cluster: {type: kind, name: stack-test}
  ui:      {command: ..., url: ..., profile: test}   # profile points at kind, never prod
  tools:   [kubectl, agent-browser, ...]

steps:
  - id: 1
    kind: shell | browser
    description: "what a human would say this step does"
    do: [...]        # commands; browser steps use agent-browser
    assert: [...]    # conditions to verify after
    timeout: 30s     # optional
    note: >          # optional: expected-but-not-failure context
```

Plus a **runner contract** at the bottom of the file: execute in order, substitute refs, no interpretation, no retries, always teardown, fixed report format.

## Browser automation: agent-browser (not Playwright)

Installed: `npm install -g agent-browser && agent-browser install` (v0.42.0, Chrome 157). A brew formula exists but npm was more reliable on this machine.

Why it fits the pipeline: it's a CLI built for AI agents with **compact text output** and a **ref-based snapshot system**:

```
agent-browser snapshot -i     # accessibility tree with stable refs: @e1, @e2, ...
agent-browser click @e54      # click by ref, not CSS selector
agent-browser wait --text "done"
agent-browser open http://localhost:8097
agent-browser screenshot page.png
agent-browser close
```

The runner snapshots before each interaction and finds the element by text/role. **No CSS selectors to rot** when the UI changes. 50+ commands total (forms: `fill`, `type`, `check`, `select`, `upload`; states: `is visible`, `is enabled`, `is checked`).

## Verified end-to-end (the proof from today's session)

- **kind + colima**: `colima start` (needed `brew install docker` first), `kind create cluster --name stack-test` → Ready in **17s**. kind 0.33.0 was already installed; colima was the missing Docker runtime.
- **Drove the real stack installer UI** (localhost:8097) with agent-browser: profiles index → clicked through to the prod profile page → full state visible and navigable: Check stage with last-result badge, 5-app table with release dropdowns, runs table, Install stage with 5 checkboxes + "Install all 5" button.
- **Deliberately did NOT** click Run check / Install on the prod profile — it points at real GKE prod.
- **Added a `test` profile** to `~/.config/stack/profiles.yaml` (dogfood example stack → `~/.kube/config` → kind cluster) so the pipeline can click Install safely.
- Cleaned up: browser closed, kind cluster deleted.

## Open items / next steps

1. **The runner doesn't exist yet.** Today the steps were executed by hand (an agent driving agent-browser interactively). Need a runner that reads the YAML and executes it deterministically. Shape undecided: bash script, pi session, or small purpose-built thing.
2. **The `test` profile's install only partially works** — the dogfood stack needs gh auth, cbl (cabal bootstrap), and a registry. On a bare kind cluster the install fails partway, which is *expected*: this e2e test verifies UI mechanics (buttons fire, runs start, output streams, check renders a result), not a full successful deploy. Decide later whether we want a second, deeper test for real installs.
3. **The Translator as its own agent** — today the translation was done inline by the session agent using a screenshot plus UI knowledge. Plan: make it a specialised agent/skill whose inputs are the criterion + rendered UI only.
4. **Iterate on failure, not upfront spec.** The YAML schema and runner contract will change the first few times the runner actually executes a plan. That's intended.

## Side quest (parked, separate from this pipeline)

- `~/src/git/techne/scripts/concurrency_test.py` — standalone script for techne's multi-session write-lock behaviour (N concurrent `observe` writers vs. a long-held `session-end` write lock; tests the 5s busy_timeout). Written, not yet run.

## Environment notes (so nobody has to rediscover these)

- **Local model with vision:** llama.cpp `qwen3.8-27b` on 127.0.0.1:8889. It was running *without* vision; restart it with `--mmproj /Users/shaz/models/Qwen3.8-27B/mmproj-F16.gguf` to get image input. Also added `"image"` to the `input` array for `qaf-qwen` and `spark-unsloth` in `~/.pi/agent/models.json`. Reading screenshots works best by sending the image directly to `/v1/chat/completions` (pi `--print` with image input timed out; the direct API call returned a full description fine).
- **pi `--offline`** disables startup npm installs. Required in `techne/scripts/pi_dogfood.sh` when offline — it spawns child pi sessions that otherwise hang on `npm install remote-pi` with no network (patched: `/Users/shaz/tmp/techne/techne/scripts/pi_dogfood.sh` now passes `--offline`). When online, `--offline` caused its own timeouts; drop it if network is up.
- **colima** left running at end of session; `colima stop` at end of day.

## Artifacts

| What | Where |
|------|-------|
| Test plan v2 | `~/src/git/stack-installer/tests/e2e/stack-installer-e2e.yaml` |
| Techne concurrency script (unrun) | `~/src/git/techne/scripts/concurrency_test.py` |
| Dogfood `--offline` fix | `/Users/shaz/tmp/techne/techne/scripts/pi_dogfood.sh` |
| Test profile | `~/.config/stack/profiles.yaml` (the `test` entry) |
