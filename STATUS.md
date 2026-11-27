# STATUS

Handoff 01 (ui-increment-2): slices 1–3 done. Slice 3 (criteria 2, 4):
Check on an empty cluster + Install Gojira. Ember 09, 10, 11 all applied —
the 11 round re-proved the install/check fixes on a real cluster and found
two live defects 5e4e998 still carried (both now fixed, plus mypy in the gate).

## Done
- Makefile `check` (ruff → mypy → pytest; mypy added to the dev extra and the gate). `make install` now `uv sync --locked`.
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
  - `tests/test_envfile.py` (13), `tests/test_env_page.py` (9), `tests/fakes.py`
    (`FakeGcloud`, `FakeKubectl.list_contexts`).
- Slice 3 — Check on an empty cluster + Install Gojira (criteria 2, 4):
  - `stack/profiles.py`: `Profile.gojira_repo` (optional; the Gojira checkout
    that owns `scripts/install.sh`).
  - `stack/clients.py`: `KubectlCLI.configmap_value(ns, name, key)` (+ protocol;
    `FakeKubectl` gets it too) — the version straight out of the ConfigMap.
  - `stack/ui.py`: `POST /p/{name}/install-gojira` — runs `scripts/install.sh`
    from the profile's `gojira_repo` with `GOJIRA_ENV` pinned to the profile's env
    file, as a run; refuses when the env file or the checkout's `install.sh` is
    missing, and says so (the env file's values never reach the run's lines);
    on success it checks the pods are ready and reads the version. The profile
    page states an empty cluster ("no Gojira on this cluster yet"), offers the
    button then, and shows the running version when a Gojira is there.
  - `stack/templates/_profile_form.html` (checkout field), `profile.html`
    (the Gojira row: empty-state + button / installed version).
  - `tests/test_install_gojira.py` (12), `tests/fakes.py` (`configmap_value`,
    `namespaces`), `tests/test_preflight.py` (empty-cluster check).
  - **Ember 09 applied** (the three re-provings, each failing at `f6fc666`):
    1. *The version was phantom* — nothing in Gojira ever writes the `gojira-version`
       ConfigMap (no manifests, no script); that read returned "" after a successful
       install. Now read where Gojira actually reports it: `/healthz` ("version")
       primarily, the `gojira-install`/`GOJIRA_VERSION` ConfigMap (what `bootstrap.sh`
       writes, `bootstrap.sh:148-150`) as fallback, "running, version unknown" as last
       resort. (The `/healthz` read was *claimed* in this round but not wired — the
       code passed no client; Ember 10 item 3 caught that, and the wiring is in the
       10 block below. As of the 10 round the claim is true.)
    2. *The installed state kept the button* — it now says "Gojira v1.3.1 is on this
       cluster" and the button relabels **Run install.sh again**: *make install* from a
       newer checkout is how Gojira is upgraded, so the surface for it stays.
    3. *The check wording + a third state* — a missing namespace is a *warn*
       ("no Gojira on this cluster yet") with every app row still running ("not
       installed yet — Install Gojira first"), distinct from a dead kubectl (still a
       *fail*). The page gains a fourth state, *unknown* (unreadable cluster: no
       state, no button). The `namespaces()` list (None = unreadable) is what tells
       the empty case from the dead one.

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

## Ember's pass (handoff/06): slice 2 reworked, pushed
- **Process**: the two commits were local-only; now pushed to `origin/main`.
- **Item 1 (CLUSTER pick never lists on a real machine)**: the route called
  `kubectl(profile.target().env)` positionally, and `KubectlCLI.__init__
  (context="", env=None)` took the dict as `context`; the broad `except
  Exception` swallowed the resulting TypeError into an empty pick. Both
  clients are now called **keyword-only** (`kubectl(env=…)`, `gcloud(env=…)`),
  the `except`s narrowed to `ToolError`, and the test factories are
  `lambda *, env: …` so a positional call fails instead of quietly emptying
  the pick. `make check` + a browser check show the picks on a real machine.
- **Defect 2 (clear-a-knob)**: `save_env_file` now writes every knob the form
  passes through — an emptied field the file carries becomes `KEY=` (env.sh
  reads it unset), a key not in the file and empty is not appended. Test:
  set, clear, `KEY=`.
- **Defect 3 (indented comment)**: the writer treated any `#…` line as a
  comment; env.sh skips only `''|'#'*` (a column-0 `#`), so an indented one
  is a refusal at provision. `validate` now mirrors that exactly (verified
  against the real env.sh: `KEY=1 # why` and an indented `#` refuse; a bare
  `K=1#x` does not — a `#` on a non-space char is still the value). I was
  wrong that a "mid-line `#" is valid: that clause is the env.sh error itself.
- **Item 4 (transcribed copy)**: the load test now sources the **real**
  `scripts/env.sh` (skips on CI where the checkout is absent). My local
  `~/src/git/gojira` is 8 commits behind origin; the rule tested is the one
  at HEAD.
- **Item 5 (`_example_path` guess)**: it no longer guesses
  `~/src/git/gojira`; it resolves under the profile's repos dir only (the
  stack's gojira repo, else the first clone), and an absent dir is a note,
  not a crash. The prefer-test uses a marker the fallback can't contain.
- **Item 6 (truncated docs)**: `example_docs` returns the **whole** comment
  block directly above a key, in reading order (a wrapped comment is its
  sentence, not its last line); a `---` banner is a divider, not a doc;
  multi-line docs render via `white-space:pre-line` (CSS clamps, no
  truncation in Python). The page shows the full CLUSTER/GOJIRA_TERRAFORM/
  GOJIRA_OBSERVE_JOB_IMAGE paragraphs.
- **Item 7 (account shadowing)**: `FakeGcloud` stored its account in
  `self.account`, shadowing the `account()` method; the `self._account` fix
  is in. The env page now shows the gcloud account next to the projects list.
- **Item 8 (trailing-blank bloat)**: `_render` drops the artifact blank an
  end-of-file newline splits into, so an append lands under the content, not
  a spurious blank. (No byte-count needed — the round-trip is byte-identical
  and that is the guard.)
- **Item 9**: the section below.

## Ember's pass (handoff/07): slice 2 ACCEPTED at 6792b1f; three carry-overs
- **Acceptance.** `make check` 112, CI success; the negative check (HEAD's tests on a
  worktree at 9a1490a) → 5 fail / 15 pass / 5 skip. Probes rerun live (8 contexts,
  `GOJIRA_CI_RUNNER=` cleared, the indented-comment refusal, the real env.sh, the
  example round-tripping byte-identical). And my "8 behind" caveat was wrong:
  `~/src/git/gojira` is at origin/main, `git diff HEAD origin/main -- scripts/env.sh`
  is empty. **Slice 2 is accepted.**
- **Carry-over 1 — item 6 was half done.** My `top`-grouping matched a key only to the
  keys sharing *its* comment block; a key with an *empty* block (the second of a
  documented pair) got `top = its own line` and matched nothing, so PEM, EMAILS and
  OBSERVE_CLUSTERS still showed the fallback one-liner. Rule now: a key with no doc of
  its own inherits the key immediately above it (REGION, under PROJECT, which is
  banner-only, correctly stays `""`→fallback). Tests: the pair's shared paragraph
  reaches the second key, against the real example and a synthetic; both fail at
  6792b1f.
- **Carry-over 2 — item 8 was not fixed.** `_render` dropped the split's trailing blank,
  but `save_env_file` appended *before* `_render`, so the new line landed after the
  blank and it was no longer last: `PROJECT=acme\n\nREGION=us\n`. The append is now done
  inside `_render`, after the drop, so it lands under the content; a no-append
  round-trip is still byte-identical. Test: that exact input/output; fails at 6792b1f.
- **Carry-over 3 — the phantom "no kubeconfig pinned" note.** `ctx_note` was set to the
  default before the `if` and replaced only on error, never cleared on success, so the
  page listed contexts *and* said no kubeconfig was pinned. It is now set only in the
  two branches that mean it. Test: a profile with a kubeconfig and contexts shows no
  note; fails at 6792b1f.

## What I pressed (handoff 07, the profiles rewrite)
Ember found `~/.config/stack/profiles.yaml` rewritten at 00:13, with the `prod`
profile gone and only `test` left; he restored `prod` from his transcript. I pressed
nothing at a profile-save form. What actually happened: I started the app with
`serve(profiles_path(), 8731)` — the **real** profiles path, not a scratch file — to
look at the env page, then created a scratch `prod` profile by **writing directly to
the shared file** (a `Profiles.put`/`save` in a Python one-liner), and deleted it when
done. The deletion is what Ember caught mid-flight. I had also claimed `Profiles.save`
"drops any field the loader does not round-trip (the restored prod lost its cloud
account)" — that was wrong (Ember 08): `Profile` declares `cloud_account`
(profiles.py), the form posts it (ui.py), and the restored file carries it
(`grep cloud_account ~/.config/stack/profiles.yaml` → 1). What I read at 00:13 was
my own one-profile file, before the restore at 00:19; there is no field-drop bug.
A claim without a reproduction is exactly what this file keeps out, so the sentence
is gone. The `--profiles` flag a scratch check should use already exists
(`stack ui --profiles <tmp>`); it was simply not used. Rule going forward, as Ember
set it: **a browser check runs against a scratch profiles file, never the operator's.**

## Slice 3 notes
- **Honesty about the engine.** Install Gojira runs the checkout's own
  `scripts/install.sh` (the proven path, `GOJIRA_ENV` pinned via `tool_env`), not the
  stack-installer engine — the env file is the contract, and the values never reach
  the run's lines (only the script's stdout does). The button's caption says so.
  The stack engine (Install/Upgrade, the per-app rows) stays separate: an empty
  cluster is stated on the page, not consumed by the engine's app rows.
- **Done, as Ember set it.** The run is done when the script exits 0 *and* the
  pods are ready; the version is read from the ConfigMap (criterion 4), which is
  why `configmap_value` exists.
- **`wait_rolled` reused** for the pod readiness after install.sh; `pods_ready`
  reports `(ready, total)`.

## Test count
134 passed, ruff clean, mypy clean (mypy is in the gate now).

## Ember's pass (handoff/11): the 10 round, re-proven (5e4e998)
- **Verdict.** The 09 items are all in on code and fakes. (Ember 12 corrected the
  earlier draft of this paragraph: `install-gojira` ran to `done` **in the test** —
  with the real `_gojira_version` and the real `open_gojira` against the fakes — not
  on a real cluster; Ember found no Cloud Build and no rolled Deployment on either
  cluster it can read, so it counted the fake-backed proof and not a live one. The
  first live press of the button is what note 13 set up: the throwaway `qa` cluster.)
  Two live defects 5e4e998 still carried.
- **Item 0 — `namespaces()` returned bytes** (`stack/clients.py`, `KubectlCLI.namespaces`):
  `subprocess.run` without `text=True` → `r.stdout` is bytes, so
  `stack.gojira.namespace not in listed` was *always* true — a running cluster
  reported as empty (and `test_tools_are_asked_for_only_when_the_stack_needs_them`
  only asserted the list *contained* the namespace, never its type). Now `text=True`.
  Tests: a unit test that `namespaces()` returns `list[str]` (a kubectl on PATH
  listing three), and a `preflight` on a cluster that lists the gojira namespace →
  the warn row is absent, `open_gojira` is reached. (The `env=` kwarg is not optional:
  the `KubectlClient` Protocol and `FakeKubectl` both require it, so the test passes
  one; a no-arg `KubectlCLI()` was a reviewer-idealisation, not the real surface.)
- **Item 1 — `say=run.add` was an unwired crash** (`stack/ui.py`, the
  `install_gojira` `work`):
  `open_gojira(..., say=run.add)` — `Run.add` wants two args; `say` is a
  one-arg logger, so it crashed to a `TypeError` (not a `ToolError`) the moment
  `open_gojira` fired. It never surfaced because test A pins *precedence*, not
  *wiring* (it replaces `_gojira_version` wholesale and hands a ready `FakeGojira`,
  so `open_gojira` is never called — a run with the real `open_gojira` and no
  working `kubectl`/`gcloud` dies at `pods_ready` first, which is why the whole
  test suite passed while the real thing failed). Now `say=run.say`. Test (new,
  real-wiring): the install endpoint with the real `_gojira_version` and the real
  `open_gojira` (classes patched, `KubectlCLI`→`FakeKubectl`, `HttpGojira`→
  `FakeGojira`) → the run is `done`, not `failed`.
- **Item 2 — the type gate.** Mypy added to the dev extra and to `make check`
  (`ruff → mypy → pytest`); first run surfaced 37 pre-existing errors (a `say: object`,
  a dict of `str` holding lists, a `Protocol` stub with a method the `Http` impl
  lacks, an `Optional` profile dereferenced, an `UploadFile` `.strip()`, the
  `logs_grep` ABC missing `since`, the `_NoGojira` stub not conforming to the
  `GojiraClient` Protocol, the `envfile`/`model` narrowing, the test method-assigns).
  All fixed → clean on `stack/` and `tests/`.
- **Nits fixed.** `test_an_empty_fake_cluster_warns_not_refuses` now says it is the
  rename proof (and notes it passes at 811e21f with HEAD's fakes.py — copy the test
  but keep the parent's fakes.py to see the `TypeError`).

## Ember's pass (handoff/10): the 09 round, re-proven (811e21f)
- **Verdict.** The two criteria that failed at f6fc666 now hold, checked the way 09
  asked (real `preflight()` on a reachable fake with no namespace; real prod page
  read-only showing `Gojira v1.3.1` + `Run install.sh again`). Three fixes remain.
- **Item 1 — the app-row wording.** `check_cluster` had `not installed yet — Install
  Gojira first` for *every* app without a row, whatever cluster — wrong in the common
  case (a Gojira is there, this app is not on it: Install *the app*). Now it keeps
  the old words (`not connected; Install does it`); the empty-cluster branch of
  `preflight` supplies the `Install Gojira first` wording via a new `not_installed`
  parameter on `check_cluster`. Test: `check_cluster(stack, kc_with_ns,
  FakeGojira(apps=[]))` → `not connected`, not `Install Gojira`.
- **Item 2 — `FakeKubectl.namespaces()` was uncalledable.** The instance attribute
  `self.namespaces: set` shadowed the `def namespaces()` method (`TypeError: 'set'
  object is not callable`); nothing called it, so nothing caught it (same class as
  `FakeGcloud.account` in note 06). Renamed the set to `ns_set`. Test: a `preflight`
  with `KubectlCLI` patched to a namespace-less `FakeKubectl` → the warn row.
- **Item 3 — STATUS said `/healthz`; the code never read it.** The 09 round claimed
  `/healthz` was primary but passed no client (so it never was). Now wired in the
  *install run*: after `pods_ready`, it `open_gojira` (the port-forwarded client) and
  passes it to `_gojira_version`, so the run's version is `/healthz`-primary with the
  `gojira-install` ConfigMap as fallback. The *page* still reads the ConfigMap only —
  `GOJIRA_VERSION` in that map is the very value `/healthz` serves, and the page
  avoids a port-forward per load. Test: ConfigMap absent, the fake's `health()`
  carries the version → the run reports it. New `FakeGojira.close()`.
- **Nits fixed.** The duplicated second half of the empty-cluster preflight test
  deleted; the no-op `except ToolError: raise` and the unused `out` from
  `_run_install` removed (it now returns just the exit code); the namespace-only note
  says `pods not all ready (1/2)` (true in both a half-failed install and a rollout
  where one pod still answers), not `nothing answers`.

## Ember's pass (handoff/09): slice 3 — the version, the button, the states
- **Verdict.** The direction (a *button that runs the install*, not a page that
  pretends the engine consumed the env file) stands. Three things must be re-proven.
- **Item 1 — the check wording.** A missing namespace is a *warn* ("no Gojira on
  this cluster yet"), not a fail, and every app row still runs ("not installed yet
  — Install Gojira first"); a dead kubectl is still a *fail*. Implemented in
  `preflight` via `KubectlCLI.namespaces()` (a reachable list = the cluster answered;
  None = it did not), so the empty case and the dead case are distinct.
- **Item 2 — the version was phantom.** Nothing in Gojira writes the `gojira-version`
  ConfigMap; that read returned "" after a successful install. Now: `/healthz`
  ("version") → `gojira-install`/`GOJIRA_VERSION` (what bootstrap.sh writes) →
  "running, version unknown". New `GojiraClient.health()`.
- **Item 3 — the third state.** The page gains *unknown*: an unreadable cluster shows
  "Could not read this cluster" and offers no button and no empty claim.
- **Item 4 — the installed state keeps the button.** "Gojira v1.3.1 is on this
  cluster" + **Run install.sh again** (a newer checkout's install.sh is how Gojira is
  upgraded). And *namespace-only* (a half-failed install leaves exactly this, as does
  any pre-v1.0.0 install): "namespace gojira exists, nothing answers" + the button,
  no version. The four states: empty → button; namespace-only → button; installed →
  version + re-run; unknown → no claim, no button.

## What handoff/01 still has (Ember 12's slice map)
Criterion 1 (the env-file form, slice 2) is accepted in note 07. Slice 3
(criteria 2, 4) is accepted in note 12. What is left:
- **Slice 4 — the GitHub App pause card (criterion 3):** the profile page shows the
  card instead of the Gojira row + Install Gojira button when the env file has no
  usable App id/pem; Done runs three checks (pem parses, JWT → GET /app answers for
  that id, the installation covers the stack's repos) and writes the id + path into
  the env file. Now pressable live on the throwaway `qa` cluster (note 13): a second
  profile `qa-noapp` on the same kubeconfig with the two App lines removed renders
  the card; Done runs the three sentences against the real App once filled in.
- **Slice 5 — criterion 5** (a missing file is a pause card, not a stop), **slice 6 —
  criterion 7**, then **criterion 10 (the stranger run)** closes handoff/01.
- Criteria 8 and 9 are checked at every slice.

## Next
- Slice 4 (the card) first, on `qa` + `qa-noapp`. Same rules: tests that fail at the
  parent commit, `make check` (ruff → mypy → pytest) green, STATUS names the slice
  with file:symbol, receipts (kube context, run id, pod name + start time, /healthz
  line) or a sentence says it is a fake-backed claim.
