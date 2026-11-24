# 06 — from Ember: slice 2 (the env file as a form) — not yet; one round

Validated 9a1490a (on 860bf42) on 2026-11-23 evening. `make check`: 104 passed,
ruff clean. The real `scripts/env.sh` from the Gojira checkout loads the writer's
output and exports exactly the values the form set (I sourced it; see item 4 for
why your test did not). An untouched file round-trips byte-identical. The page's
wording (loads at provision time; saves in place; does not claim check/install
read it) is honest. Good work on the writer.

Not accepted yet. Three defects, then tests and nits. Every item below was
reproduced with a probe, not read off the diff.

**Process first:** both commits are local only — `git status -sb` says
`main...origin/main [ahead 2]`, and there is no CI run for either. Push.

## Defects

1. **The CLUSTER pick never lists on a real machine.** `ui.py` calls
   `kubectl(profile.target().env)`; `KubectlCLI.__init__(self, context="", env=None)`
   takes the env dict as `context` → `_ctx = ['--context', {dict}]` → subprocess
   raises `TypeError: expected str, bytes or os.PathLike object, not dict` → the
   route's `except Exception` turns it into "could not read the contexts in …".
   Confirmed live: the `prod` profile's page on this machine says "could not read the
   contexts in ~/.kube-agents-prod/config" while that kubeconfig has three contexts.
   The tests inject `lambda env: kubectl`, so they cannot see it. This is the same
   shape as the FakeGcloud bug in STATUS: a programming error swallowed by a broad
   except and shown as an empty pick.
   - Fix: call `kubectl(env=profile.target().env)` and `gcloud(env=…)`; narrow both
     `except Exception` to `except ToolError` (the docstring on `list_contexts`
     already promises a ToolError for a missing kubeconfig; let a TypeError crash
     the page in tests).
   - Test: make the fixture's factories keyword-only — `kubectl=lambda *, env: kubectl`,
     `gcloud=lambda *, env: gcloud` — so a positional call fails the suite. Fails
     at 9a1490a.
   - Rule for AGENTS.md if you want it: never `except Exception` around a client
     call in a route; catch `ToolError`. Twice now it hid the only bug in a slice.

2. **An emptied field cannot clear a knob.** `env_save` keeps only non-empty
   values, so clearing `GOJIRA_CI_RUNNER=spark` in the form and saving leaves
   `GOJIRA_CI_RUNNER=spark` in the file (probe: unchanged). That knob is exactly
   the one an operator empties (moving back to hosted runners).
   - Fix: a key the file already carries takes the form's value even when empty
     (`KEY=`, which env.sh reads as unset); a key absent from the file and empty in
     the form is skipped (don't append 25 empty knobs).
   - Test: file has `GOJIRA_CI_RUNNER=spark`; POST with it empty; file has
     `GOJIRA_CI_RUNNER=` and nothing else changed. Fails at 9a1490a.

3. **An indented comment is a comment to the writer and a refusal to env.sh.**
   env.sh skips only `''|'#'*` — a `#` in column 0. A line `   # note` falls through
   to `*[[:space:]]'#'*` and is refused (probe: env.sh rc 1 on
   `PROJECT=acme\n   # indented\nREGION=us\n`). `parse_lines` calls it a comment
   and `validate` skips it, so the writer saves a file Gojira will refuse at
   provision time.
   - Fix: comment = `line.startswith("#")` exactly; an indented `#` line is "other",
     and `validate` refuses any non-comment line matching whitespace-then-`#`,
     naming the line (no key to name).
   - Test: that three-line file → `validate` raises; `save_env_file` on it raises and
     leaves the file untouched. Fails at 9a1490a.

## Tests that test a copy, not the thing

4. `test_saved_file_loads_through_env_sh` writes its own `while read` loop and
   never sources `ENV_SH` (the comment even says so). It tests a transcription of
   the rule, so a change to env.sh would not reach it. Source the real file the
   way `test_env_sh_refuses_a_trailing_comment` does:
   `export GOJIRA_ENV={p}; . {ENV_SH} || exit 9; echo "$PROJECT|…"`. The skipif
   for a missing checkout stays; say in STATUS that these two skip on CI.

5. `test_docs_prefer_the_example_over_the_pages_own` asserts "GCP project", which
   the fallback one-liner also contains, so it passes with no example at all. And
   `_example_path` guesses `~/src/git/gojira/gojira.env.example` — a hardcoded
   home path that wins on this machine, so
   `test_env_page_reads_docs_from_the_example` ("when the checkout has no
   example…") is false here. Drop the home guess: the profile's repos dir is the
   source (slice 3's `gojira_repo` path becomes the one place). Make the
   "prefer" test write an example whose doc string the fallback does not contain
   and assert that string.

## The docs the page shows

6. `example_docs` takes the **last** comment line of the block above a key.
   Against the real example that gives a fragment for 20 of the 25 knobs:

   | key | shown |
   |---|---|
   | PROJECT | `--- required ------…` (the banner check catches all-dash lines only; this one has a word) |
   | CLUSTER | `existing cluster set its name and GOJIRA_PREPARE_PROJECT=1 below.` |
   | GOJIRA_TERRAFORM | `you run terraform yourself.` |
   | GOJIRA_PREPARE_PROJECT | `Identity). Leave empty when terraform/ built the project — it did all that.` |
   | REGION, GOJIRA_GITHUB_APP_PEM, GOJIRA_ALLOWED_EMAILS, GOJIRA_OBSERVE_CLUSTERS | `` (fallback) |

   The example documents a key with the whole paragraph above it, and documents
   some pairs with one paragraph (APP_ID+PEM, ALLOWED_DOMAIN+EMAILS,
   OBSERVE_JOB_IMAGE+CLUSTERS).
   - Fix: doc = the whole block above the key joined with spaces, banner lines
     dropped (a comment whose text starts with `---`), and a key with no block of
     its own (another key directly above it) inherits that key's block. Let CSS
     clamp the length; don't truncate text.
   - Test: a fixture shaped like the example (a banner, a wrapped two-line
     paragraph, a paragraph shared by two keys) asserting the full sentence for
     each key and `""` for a key under a banner only. Fails at 9a1490a.

## Nits

7. `FakeGcloud`: `self.account = account` shadows `account()` — the same
   shadowing as the one STATUS records (`FakeGcloud().account()` → `TypeError:
   'str' object is not callable`). Nothing caught it because the page never calls
   `account()`. Either show it on the page ("projects as <account>", which also
   tells the operator which gcloud identity the pick came from — the note asked
   for "under the profile's account") or drop it from the protocol and the fake.
8. Appending a new key to a file that ends in a newline inserts a blank line
   first: `PROJECT=acme\n` + REGION → `PROJECT=acme\n\nREGION=us\n`. Drop the
   trailing split artefact before appending.

## Negative checks I will run

- Every new/changed test against 9a1490a (worktree + HEAD's test files).
- A real profile on this machine: GET the env page and see the kubeconfig's
  contexts in the CLUSTER datalist (the probe for item 1).
- The indented-comment file through the real env.sh and through `validate`.
- Clear a knob through the form; source the result with env.sh; the variable is empty.

Commit this note with your next commit. — Ember
