# STATUS

Handoff 01 (ui-increment-2), slice 1 of 6: Install waits for the roll (criterion 6).

## Done
- Makefile `check` (pytest + ruff). `make install` now `uv sync --locked`.
- Slice 1 — install's done is the rolled deployment:
  - `stack/steps.py`: `install_app` ends image apps in `wait_rolled(app, digest)`.
  - `tests/test_install_roll.py`, `tests/fakes.py`: probes, scripts, fakes.

## The mark + saw_running rule (final, note 03)

`wait_rolled` always marks the latest run's `(status, conclusion)` when the
watch begins, plus a `saw_running` flag (it saw the run in flight under the
watch). The stop: the run completed without success AND (the tuple differs
from the mark OR `saw_running`). Waiting states (`queued`, `in_progress`)
never stop. The `mark: bool` parameter is gone.

This restores the old `wait_rolled`'s fast-fail (stack#13) without the
mark-only defect: an in-flight→failure transition whose tuple equals the mark
(upgrade, probe E) stops; a healthy `queued → in_progress` (install, probe A)
and an earlier run of the same shape (install, probe B) do not.

**Both call sites** (install, upgrade) call `wait_rolled` bare.

## Ember's pass (handoff/02)
- Probe A (queued at mark → in progress → flip): false stop, fixed.
- Probe B (earlier run at mark, merge's run queued): false stop, fixed.
- `_pinned_digest` returns `""`.

## Ember's pass (handoff/03)
- **The mark alone can't catch upgrade failure.** The upgrade's mark IS a
  failure tuple (the pin's contract check, finding #20); the merge's run
  going in-flight then failing is the *same* tuple — `saw_running` is the
  discriminator. `wait_rolled` gained the flag (restoring the old
  `saw_running` fast-fail); probe E (`tests/test_upgrade.py`) covers it.
  Probe E fails on the mark-only commit, passes now.
- Old code left an implicit `saw_running` (it caught failure on
  in-progress→complete); the new code makes it explicit.

## Test count
87 passed, ruff clean (was 83 before notes 02/03; +4 tests: probe A, B, E,
and the `_upgrade` helper's `rollout_seconds` param).

## Next
- Slice 2 (the env file as a form) — does not depend on slice 1.
