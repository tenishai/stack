# 04 — from Ember: slice 1 ACCEPTED at edd4e21; one small finish; slice 2 begins

## What I ran on edd4e21

- `make check`: 87 passed, ruff clean. CI run 45278365864 green.
- Negative check: `tests/test_upgrade.py::test_a_failed_post_provision_run_stops_fast`
  run against bdab038's `stack/steps.py` (worktree, HEAD's test file copied in) FAILS
  with "pods not on … after 2s" — the old behaviour, the run went to the window.
  `test_a_roll_that_never_lands_times_out` passes on both commits. Probes A and B
  still pass.
- A second probe E that drives `_upgrade(...)` (so `upgrade_app` → `settle_setup_pr`
  → `wait_rolled`) instead of calling `wait_rolled` directly: stops in 0.06 s at
  edd4e21 with the Actions URL; at bdab038 it runs to the 3 s window. The real
  upgrade path has its fast stop back. Slice 1 is accepted.

## One finish, with slice 2 (not a round of its own)

Your committed probe E calls `inst.wait_rolled(...)` directly. The rule is proven,
but the thing I asked for in note 03 was the *upgrade path*: if someone later
moves or re-marks the call in `upgrade_app`, the direct-call test stays green and
the regression comes back unseen. Make probe E go through `_upgrade`. The version
below passes at edd4e21 and fails at bdab038 (same flipping closure as yours, same
timing assertion); fold it into `tests/test_upgrade.py` in place of the direct call,
or keep both.

```python
def test_a_failed_post_provision_run_stops_fast(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    github.runs["org/kobo-deploy"] = ("completed", "failure")   # the pin push's contract check
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{OLD_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    state = {"n": 0}
    def _poll(ns, name):
        state["n"] += 1
        if state["n"] == 1:
            github.runs["org/kobo-deploy"] = ("in_progress", "")
        elif state["n"] == 2:
            github.runs["org/kobo-deploy"] = ("completed", "failure")
        return kubectl.images.get("kobo", "")
    kubectl.deployed_image = _poll
    t0 = time.monotonic()
    with pytest.raises(ToolError, match="deploy run failed"):
        _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane,
                 rollout_seconds=3.0)
    assert time.monotonic() - t0 < 1.0
```

Your honest note about driving the fake's run state from the `deployed_image`
closure is fine by me: the fake has no clock, and the acceptance criterion is
"the stop arrives well inside the window", which the assertion states.

## Slice 2 — the env file as a form

Per `handoff/01-ui-increment-2.md`, slice 2. Nothing in it depends on slice 1.
Same rules: tests that fail at the parent commit, `make check` green, STATUS row.
Commit this note with your next commit.

— Ember
