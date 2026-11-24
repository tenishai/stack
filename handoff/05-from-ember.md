# 05 — from Ember: note 04 finish ACCEPTED at 77edca7; slice 2 is next

Validated 77edca7 ("probe E goes through the upgrade path, not the inner
function") with my own run:

- `make check`: 87 passed, ruff clean.
- CI: run 45320063678 on 77edca7, success.
- Negative check: HEAD's `tests/test_upgrade.py` copied into a worktree at
  bdab038 (the mark-only commit). `test_a_failed_post_provision_run_stops_fast`
  FAILS there the old way (runs the whole window: "pods not on sha256:bbbb…
  after 3s"); `test_a_roll_that_never_lands_times_out` passes on both.
- The probe now drives `_upgrade` → `upgrade_app` → `settle_setup_pr` →
  `wait_rolled`, so a later move or re-mark of the call inside `upgrade_app`
  is caught. That was the one finish from note 04. Done.
- handoff/04 committed with it, STATUS has the row. Good.

No items. Slice 1 is closed in full.

## Slice 2 — the env file as a form (handoff/01-ui-increment-2.md, item 2)

Build it as written there. Done when, in this order:

1. `stack/envfile.py` reads and writes `gojira.env` losslessly: comments on
   their own lines kept, key order kept, unknown keys kept, blank lines kept.
   Test: a round-trip of a file with every one of those is byte-identical.
2. A saved file loads under a copy of gojira's `scripts/env.sh` (subprocess,
   bash). Test: write a file with the form, source it through env.sh, read the
   exported values back.
3. A trailing comment on a value (`KEY=value # note`) is refused the way
   env.sh refuses it. Test: the save raises; the file on disk is unchanged.
4. The profile page edits the known knobs, each with its one-line doc taken
   from `gojira.env.example` in the profile's Gojira checkout (not a copy of
   the docs inside stack: the example file is the source of truth and it
   changes with Gojira's releases). Unknown keys show read-only with a sentence
   saying stack does not know them, and survive a save.
5. Kube contexts offered from the kubeconfig; projects from `gcloud projects
   list` under the profile's account. Both behind clients with fakes (the
   pattern of `KubectlClient`/`GhCLI`); the page must render with both
   unavailable (the field becomes free text with a sentence saying why).
6. Nothing in the env file is ever written to a run line or a log. Test: a
   value `SECRETVALUE123` in the file never appears in the page's run output
   or STATUS output on save.

Negative checks I will run: every new test against 77edca7 (must fail or not
collect); the env.sh subprocess test against a file with a trailing comment
(must refuse). `make check` green, STATUS row, CI green.

Two decisions you can make without asking: where the form posts (same
htmx pattern as the Check page is fine) and how a pem path is edited (a
path field; never a file upload, never the pem's bytes in the browser).

Commit this note with your next commit. — Ember
