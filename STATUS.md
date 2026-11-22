# STATUS

Slice 1 of 6 (handoff/01-ui-increment-2.md): Install waits for the roll (criterion 6).

## Done
- Makefile with `check` (pytest + ruff) so the road-rule is enforceable.

## Next
- Slice 1: after the setup PR merges, an image app's install waits for the
  post-merge deploy run and for its pods to run the digest the stack file pins
  (as upgrade's wait_rolled already does); a failed post-merge run stops with
  the Actions URL. Tests on fakes first.

## Uncertain
- Whether "the run it starts" means the run dispatched by the post-merge
  /sync (provision) — I am reading it as: after settle_setup_pr on an image
  app, watch the connected repo's workflow run and the rollout, exactly the
  way upgrade_app does after its provision.
