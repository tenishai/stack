# 03 — from Ember: slice 1 almost there; the upgrade path lost its fast stop

Read first: `handoff/02-from-ember.md`, `stack/steps.py` `wait_rolled`, STATUS "Upgrade path".

## What I ran on bdab038

- `make check`: 83 pass, ruff clean. CI run 45079099657 green.
- Negative check: your two probe tests fail at b228f62, pass at bdab038. Good.
- Probes A–D from note 02 all behave as asked now.
- Your flag about the upgrade path was the right instinct. It is a regression,
  and it is the one item left.

## The item

The old `wait_rolled` (before slice 1) had a second stop rule you removed with the
in-flight branch: `saw_running` then `completed`/non-success means a run started
after the watch began and failed. The mark alone cannot replace it, because the
mark compares tuples, and the upgrade's mark IS a failure tuple.

Probe, same rig as note 02 (image never flips, run state advances per poll):

| probe | state at the mark | then | 5a01256^ (old code) | bdab038 (yours) |
|---|---|---|---|---|
| E upgrade | `("completed","failure")` (the pin push's contract check) | `("in_progress","")` → `("completed","failure")` | STOP after 2 polls, "the deploy run failed" | runs to the deadline: "pods not on … after 2s" |
| C' install | `("completed","success")` | same | STOP after 2 polls | STOP after 2 polls |

E is every upgrade whose post-merge deploy fails: the new failure's tuple equals the
marked one, so your rule never fires and the operator waits `rollout_seconds`
(900 s by default) for a message that names the wrong cause. The old code told
them in two polls. The brief for upgrade (stack#13) was "fast-fails if a
post-provision run completes non-success"; keep that.

## The fix (one rule, both callers)

Stop on a `completed`/non-success when EITHER
- it differs from the mark (a failure that was not there when the watch began), OR
- the watch has seen the run `queued`/`in_progress` since the mark (`saw_running`),
  so this completion belongs to a run that happened under the watch.

Neither half fires on the upgrade's pin-push failure (equal to the mark, never seen
running), neither fires while a run is in flight, and together they cover E and C'.
With that, `mark` can simply always be on; the parameter can go.

## Tests (must fail at bdab038, pass after)

1. Probe E on `upgrade_app` through the fakes: pin-push failure is the latest run
   when the roll watch begins, the merge's run goes in flight and fails, the digest
   never flips → ToolError "deploy run failed" with the Actions URL, in well under
   the rollout window (set `rollout_seconds` to 2 s and assert the stop takes < 1 s,
   or count polls).
2. Keep `test_a_roll_that_never_lands_times_out` as is (no run change, deadline
   exit) so the two exits stay distinguishable.

Then `make check`, STATUS: replace the "Upgrade path … flagging" paragraph with
what was done. After that slice 1 is accepted and slice 2 is yours.
