# STATUS

Handoff 01 (ui-increment-2), slice 1 of 6: Install waits for the roll (criterion 6).

## Done
- Makefile with `check` (pytest + ruff) so the road-rule is enforceable.
- Slice 1 — install's done is the rolled deployment:
  - `stack/steps.py`: `install_app` now ends image apps (a `deploy_repo` with a
    pinned digest) in `wait_rolled(app, digest, mark=True)`, the same roll watch
    upgrade ends in. Done is the deployment running the digest the stack file
    pins and its pods ready — not the post-merge run going green (the 2026-11-10
    kobo memory change said done while the old pods were still up).
  - `wait_rolled` gained `mark`: the run's state is marked when the watch begins,
    and a change toward failure — a completion that failed, or a run still in
    flight that was not — is a stop with the Actions URL. The upgrade's expected
    exception is untouched: its pin-push contract-check failure completes before
    the provision, so the watch sees no change (no false stop).
  - `_pinned_digest(app)`: the digest a stack file pins, "" when it pins by tag
    alone — those (and source builds) end where they always did, at pods-ready.
  - `tests/test_install_roll.py`: four tests on fakes (done at the digest; waits
    past the window with the URL; a failed post-merge run stops with the URL;
    source apps unchanged).
  - `tests/fakes.py`: `FakeKubectl` gained `image_script`/`image_polls`/
    `image_hooks` (the deployed image per poll) and `FakeGitHub` gained
    `leave_pending`/`complete` (pin a run's state for the watch to observe).

## Uncertain
- Read "the run it starts" as the run the merged setup PR's /sync (provision)
  dispatches — the same run upgrade_app waits on after its provision. If Ember
  reads it as a distinct post-merge run, the watch is the same shape, just the
  mark moves. Flagging for the review.
