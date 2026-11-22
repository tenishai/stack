# STATUS

Handoff 01 (ui-increment-2), slice 1 of 6: Install waits for the roll (criterion 6).

## Done
- Makefile `check` (pytest + ruff). `make install` now `uv sync --locked` (the
  lock was in the commit but unread; Ember leaned lock + sync, matching gojira —
  taken. README dev-setup line updated to match.)
- Slice 1 — install's done is the rolled deployment:
  - `stack/steps.py`: `install_app` ends image apps in `wait_rolled(app, digest,
    mark=True)`. Done is the deployment running the digest the stack file pins,
    its pods ready — not the post-merge run going green.
  - `tests/test_install_roll.py`: done at the digest; the window ends with the
    URL; a failed post-merge run stops with the URL; source apps unchanged.
  - `tests/fakes.py`: `FakeKubectl.image_script`/`image_polls`/`image_hooks`
    (the deployed image per poll), `FakeGitHub.leave_pending`/`complete`.

## Ember's pass (handoff/02), resolved — slice 1 done
- **The defect**: `mark=True` stopped on *any* change toward failure, including
  `queued → in_progress` (probe A) and an earlier completed run being replaced by
  the merge's queued run (probe B) — the 2026-11-10 kobo install shape.
  Fixed: `queued`/`in_progress` are waiting states, whatever they were at the mark.
  The mark does one job — tell an old failure (already there at the mark) from a
  new one. The stop is now only a `completed`/non-success that differs from the mark.
- **Two probe tests added** (both fail at b228f62, pass after): probe A (queued
  at mark, in progress next, flip on poll 3 → "rolled") and probe B (earlier
  success at mark, merge's run queued, flip later → "rolled").
- **`_pinned_digest`** now returns `""` (was an implicit `None`).
- **Upgrade path**: `upgrade_app` now calls `wait_rolled(app, desired, mark=True)`
  too. Ember's edit removed the old default (non-mark) failure branch the upgrade
  relied on; the upgrade has the same property as install (the pin-push's own
  contract-check failure completes before the provision, so it is old, not new),
  so `mark=True` is the same one-job mark, not a regression. Flagging in case
  Ember scoped it deliberately.

## Next
- Slice 2 (the env file as a form) — does not depend on slice 1.