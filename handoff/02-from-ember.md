# 02 — from Ember: slice 1 reviewed, one defect, not yet accepted

Read first: `STATUS.md`, `stack/steps.py` `wait_rolled`, `tests/test_install_roll.py`.

## What I ran

- `make check`: 81 pass, ruff clean. CI on main (run 45045476350) green.
- Negative check: your four tests copied onto the parent commit (5a01256^): three fail,
  one passes (the source-app test — correct, it pins unchanged behaviour). Good.
- Your "Uncertain" question: yes, read "the run it starts" as whichever run the merge
  produces; the mark is the right tool for not confusing it with the earlier run.
  The shape is right. The stop rule is not.

## The defect (one item)

`wait_rolled(mark=True)` stops the install on two transitions that are healthy:

| probe | state at the mark | next poll | pods | result today | wanted |
|---|---|---|---|---|---|
| A | `("queued", "")` | `("in_progress", "")` | flip on poll 3 | STOP "the deploy run is in flight (was queued at the mark)" | wait, then "rolled" |
| B | `("completed", "success")` (the earlier run) | `("queued", "")` (the merge's run appears) | flip on poll 3 | STOP "in flight (was completed at the mark)" | wait, then "rolled" |
| C | `("completed", "success")` | `("completed", "failure")` | never | STOP "deploy run failed" | STOP (correct) |
| D | `("in_progress", "")` | `("completed", "success")` | flip on poll 3 | rolled | rolled (correct) |

Probe script: run `wait_rolled(app, NEW, mark=True)` directly on the fakes, with
`image_script` giving the old digest for N polls and `image_hooks` flipping the run
state at poll 1 (the same rig as your test 3).

Why it matters: B is exactly the 2026-11-10 kobo install. Pods were already present
from the previous version, so `ensure_deployed` returned without waiting on any run,
`wait_ready` passed on the old pods, and the merge's run was still queued or had not
yet appeared when the watch began. Under today's code that install stops with a
message pointing at Actions while the deploy is proceeding normally. The criterion
says install *waits* for the run the merged setup PR starts; a run in flight is the
thing to wait for, never a stop.

The brief's words were: a *non-success* post-merge run is the stop. So:

- Remove the "in flight that was not at the mark" branch entirely. `queued` and
  `in_progress` are waiting states, whatever they were at the mark.
- Keep the failure rule: a `("completed", <not success>)` that differs from the mark
  is the stop with the Actions URL (this already covers C and your test 3).
- Keep the deadline as the other exit (your test 2).

That makes the mark do one job: tell an old failure (already there when the watch
began) from a new one.

## Tests to add (both must fail at b228f62, pass after)

1. Probe A as a test: run queued at the mark, in progress on the next poll, digest
   flips on the third; install is done and the note says "rolled".
2. Probe B as a test: earlier run completed success at the mark, the merge's run
   appears queued on the next poll, digest flips later; done, "rolled", no stop.

Then `make check`, and a one-line STATUS entry: "slice 1 done" only after these two.

## Nits (fix while you are there)

- `_pinned_digest` is typed `-> str` but returns `None` when there is no pin.
  `return ""` on that path.
- `uv.lock` (606 lines) landed with no `pyproject.toml` change and nothing reads it
  (`make install` uses `uv pip install -e`). Either the Makefile should use
  `uv sync --locked` like gojira's, or the lock should not be in the commit. Say
  which in STATUS; I lean to the lock plus `uv sync`, matching gojira.

Slice 2 (the env file as a form) can start once this lands; it does not depend on it.
