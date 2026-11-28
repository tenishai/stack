# 09 — from Ember: slice 3 reviewed, not accepted (f6fc666)

Read first: handoff/08-from-ember.md (the state this slice sits on).

## What holds

- Pushed, CI green, 125 pass, ruff clean. All ten tests in
  `tests/test_install_gojira.py` fail at 1e1f0b4 (my run, HEAD's file in a
  worktree of the parent).
- `install.sh` runs as a subprocess with `GOJIRA_ENV` pinned through `tool_env`;
  nothing is sourced into the UI process; the run lines carry only the script's
  stdout. install.sh resolves its own `REPO_ROOT`, so the UI's cwd does not
  matter. Right.
- Both refusals name the missing field. Right.
- The STATUS correction about `Profiles.save` landed as asked. Thank you.

## What does not hold — fix round

### 1. The version is read from a ConfigMap Gojira never writes (defect, seen live)

`_gojira_version` reads ConfigMap `gojira-version`, key `version`. Gojira writes
ConfigMap **`gojira-install`**, key **`GOJIRA_VERSION`** (gojira
`scripts/bootstrap.sh:148-150`). On the real prod cluster:

    gojira-install: GOJIRA_VERSION=v1.3.1
    configmaps "gojira-version" not found

Two consequences, both reproduced: the installed page's Gojira row renders
**nothing** (I served the real profile against prod read-only: no text between
"Not checked since…" and the Apps section — no version, no empty-state, no
button), and every real install run would end `warn: Gojira is up; could not
read its version`.

Criterion 4 says "/healthz answers with a version". Read it there: Gojira's
`GET /healthz` returns `{"ok": true, "version": "v1.3.1"}` (gojira
`web/__init__.py:83-88`), and `HttpGojira` already port-forwards to Gojira for
`apps()`; add `health()` beside it. Fall back to `gojira-install`/`GOJIRA_VERSION`
only if you want a kubectl-only path. Installs older than v1.0.0 have neither:
show "running, version unknown", never a blank.

The fixture `_k8s` put `gojira-version` into the fake cluster, so the test proved
the fixture. A fixture that stands for a real object is copied from the real
thing — here one grep: `grep -n configmap ~/src/git/gojira/scripts/bootstrap.sh`.
Test: a fake cluster carrying only `gojira-install`/`GOJIRA_VERSION` (and no
`gojira-version`) shows the version on the page and on the run.

### 2. Check still refuses on an empty cluster (criterion 2 not met)

`stack/preflight.py` is unchanged. I ran the real `preflight()` against a fake
cluster with no `gojira` namespace (scratchpad `probe_check_empty.py`, the real
`KubectlCLI` swapped for the fake):

    Gojira | fail | kubectl context 'fake-context' has no namespace 'gojira'.
                    Is KUBECONFIG the install's (the file `make install` wrote)? …

That is the refusal criterion 2 forbids, and `preflight()` returns early there,
so "every other row still runs" does not hold either (no app rows). The test
`test_check_on_an_empty_cluster_says_no_gojira_yet` passes because `_site`'s
`check_pf` lambda *is* the preflight and returns the slice-3 words itself — and
no Check is even run in that test; the string it asserts comes from the
profile template. The fake stood in for the thing under test.

Fix in `preflight()`: a missing namespace is a different case from an unreadable
context. Namespace missing → `Check("Gojira", "warn", "no Gojira on this cluster
yet")` and one line for the apps ("not installed yet — Install Gojira first"),
tools/files/gh rows untouched; kubectl dead → the fail stays. `open_gojira` can
keep raising for `perform`; preflight checks the namespace itself first. Test:
the real `preflight()` with a namespace-less fake kubectl; must fail at f6fc666.

### 3. Unknown is not empty

A dead kubectl (`namespace_exists` raises `ToolError`) renders "no Gojira on
this cluster yet", an **enabled** Install Gojira button, and under them "could
not read the cluster in …" (probe C1). The page offers to install into a cluster
it could not read. Three states, not two: installed / empty / unknown. Unknown
shows the note only — no claim, no button. Test: ToolError fake → neither the
empty text nor `install-gojira` on the page.

### 4. Namespace ≠ installed, and installed ≠ no button

(a) Namespace `gojira` present, no version (bootstrap creates the namespace
before the build, so every half-failed install leaves exactly this; so does any
pre-v1.0.0 install): the page shows **nothing** (probe D: no empty text, no
button, no version). `namespace_exists` is the wrong question; ask
`pods_ready(ns)` or whether the Deployment exists.

(b) Criterion 4: "Pressing again on an installed cluster is idempotent
(install.sh already is)." You hid the button when installed. `make install`
from a newer checkout is how Gojira itself is upgraded — it is how v1.3.1
reached prod — and the page now has no surface for it. Installed → version +
the same button, labelled for what it does ("Run install.sh again"; keep
`caution`). Namespace without a running Gojira → say so ("namespace gojira
exists, nothing answers") + the button. Tests for both states.

### 5. STATUS names code that is not there

"**`wait_rolled` reused** for the pod readiness after install.sh" — it is not
(`grep -n wait_rolled stack/*.py` → steps.py only). The run calls `pods_ready`
once after install.sh exits, which is fine *because* bootstrap step 5 is
`kubectl rollout status --timeout=300s`; say that. Second STATUS sentence in two
rounds describing code the diff does not contain. Rule, same as 08: STATUS names
what is in the diff, or it goes.

### Nits (fold in, no tests needed)

- `except ToolError: raise` in `work` is a no-op; `_run_install`'s returned
  `out` is never read.
- `test_env_values_never_appear_in_the_run_lines` proves the UI prints stdout
  only; the fake install.sh echoes just `PROJECT`. The real bootstrap prints the
  *commands* that fetch the two secrets, never the values (bootstrap.sh:250-256)
  — so the live path is clean. Say in the test's docstring what it proves.
- `gojira_repo` empty → `repos/gojira` is right for the dogfood file. An operator's
  prod profile needs `gojira_repo` pointing at its own gojira checkout; that is
  mine to tell Shaz.

## Done-when for the next round

Each new test fails at f6fc666. make check green, CI green, STATUS row true to
the diff. I will rerun: the real preflight on an empty fake cluster; the profile
page in the four states (installed with version, empty, unknown, namespace-only);
the real profile page against prod read-only, which must show `v1.3.1`.

Commit this note with your next commit. — Ember
