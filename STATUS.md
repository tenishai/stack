# STATUS

Handoff 01 (ui-increment-2), slice 2 of 6: the env file as a form (criterion 1).

## Done
- Makefile `check` (pytest + ruff). `make install` now `uv sync --locked`.
- Slice 1 — install's done is the rolled deployment:
  - `stack/steps.py`: `install_app` ends image apps in `wait_rolled(app, digest)`.
  - `tests/test_install_roll.py`, `tests/fakes.py`: probes, scripts, fakes.
- Slice 2 — the env file as a form (criterion 1):
  - `stack/envfile.py` (new): `parse_lines` / `get` / `get_all` / `validate` /
    `save_env_file` / `example_docs`. Lossless: comments, blanks, unknown keys and
    key order survive a save byte-for-byte; a value with a trailing comment is
    refused the way `scripts/env.sh` refuses it (never written).
  - `stack/clients.py`: `GcloudCLI` (account, projects) + `GcloudClient` protocol;
    `KubectlCLI.list_contexts` (every context the kubeconfig knows).
  - `stack/ui.py`: `GET/POST /p/{name}/env` — the file's values as a form, the
    example's one-line docs, PROJECT picked from `gcloud projects list`, CLUSTER
    from the kubeconfig's contexts. Saves through the lossless writer.
  - `stack/templates/env.html`, `_profile_form.html` (env-file label),
    `profile.html` (env-file link in the strip), `base.html` (knob grid CSS).
  - `tests/test_envfile.py` (7), `tests/test_env_page.py` (7), `tests/fakes.py`
    (`FakeGcloud`, `FakeKubectl.list_contexts`).

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
  `saw_running` fast-fail); probe E covers it. Fails on the mark-only
  commit, passes now.
- Old code left an implicit `saw_running` (it caught failure on
  in-progress→complete); the new code makes it explicit.

## Ember's pass (handoff/04): slice 1 ACCEPTED at edd4e21
- Ember ran `make check` (87, green), CI green, and a negative check of the
  probe against bdab038 (fails, old behaviour) plus a second `_upgrade`
  probe (stops in 0.06 s). Slice 1 accepted.
- **One finish, folded into slice 2**: probe E called `inst.wait_rolled(...)`
  directly, so a later move or re-mark of the call inside `upgrade_app` would
  keep it green while the regression came back. It now goes through
  `_upgrade` (the real path: `upgrade_app → settle_setup_pr → wait_rolled`),
  with Ember's timing assertion (stop well inside a 3 s window). Verified:
  passes at edd4e21, fails at bdab038.

## Slice 2 notes
- **Lossless, not canonical.** The writer rewrites each key the file already
  carries in place (old value when the form didn't name it — unknown keys survive)
  and appends only keys the form introduced. It never re-orders, re-writes comments,
  or blanks the file to the example's shape. The round-trip of an untouched file is
  byte-identical.
- **Docs are the example's.** `example_docs` reads the profile's Gojira checkout's
  `gojira.env.example` (the source of truth, which moves with Gojira's releases); the
  page's own one-liners in `KNOWN_KNOBS` are the fallback when no example is found.
  A key's doc is the last comment line of the block directly above it (Gojira wraps
  long comments); an all-dashes section banner is a divider, not a doc.
- **Honesty (constraint 04).** The page says Gojira loads the file when it
  provisions, and that saving rewrites values in place and leaves the rest exactly
  as written. It does not claim `check`/`install` consume the file — the install
  engine doesn't read `profile.env_file` yet (that is slice 3, Install Gojira).
- **The one bug that cost the slice.** `FakeGcloud` stored its project list in
  `self.projects`, shadowing the `projects()` method, so the route's
  `gcloud(...).projects()` raised `TypeError: 'list' object is not callable`, which
  the route's `except` swallowed into an empty pick. Renamed the attr to
  `_projects`. The page now lists projects and contexts.
- **Jinja note.** The page's pick-list is a `<datalist>` per knob (PROJECT, CLUSTER),
  the native way to offer picks without changing the input's value semantics.

## Test count
104 passed, ruff clean.

## Next
- Slice 3 (Check on an empty cluster + Install Gojira, criteria 2, 4). Check's
  cluster row says "no Gojira on this cluster yet"; the page offers *Install
  Gojira*, which runs `scripts/install.sh` from the profile's Gojira checkout
  (new profile path `gojira_repo`) with the env pinned, as a run.
- Same rules: tests that fail at the parent commit, `make check` green, STATUS row.
