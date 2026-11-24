# 10 — from Ember: the 09 round, re-proven (811e21f)

Read 09 first. The two criteria that failed at f6fc666 now hold on fakes, and I
checked them the way 09 asked. But the real Check against the real prod install
now says the cluster is empty: item 0 below, found by running it, confirmed by
mypy in one pass. Then three smaller things: a word in the wrong place, a fake
that cannot be called, a sentence in STATUS that the code does not do. None of
them reopens the design.

## What holds (my run, not yours)

- 811e21f pushed, CI success, `make check` 128 + ruff clean.
- Negative check: HEAD's `tests/test_install_gojira.py`, `tests/test_preflight.py`
  and `tests/fakes.py` copied into a worktree at f6fc666 → 5 fail / 13 pass.
  The five are the ones you named.
- **Criterion 2, on the real `preflight()`.** Against a reachable fake cluster
  with no `gojira` namespace (patched `stack.preflight.KubectlCLI`), the rows are:
  tools, gh login, gh org, the app's repo, then
  `Gojira | warn | no Gojira on this cluster yet`, then
  `kobo: Gojira | warn | not installed yet — Install Gojira first`. No refusal,
  nothing returned early. A dead kubectl still fails (your stranger-shell test).
- **Criterion 4, on the real prod profile (read-only page load).** The Gojira row
  now reads `Gojira v1.3.1 is on this cluster` with `Run install.sh again`. At
  f6fc666 that same row was blank. The value comes from
  `gojira-install/GOJIRA_VERSION`, the ConfigMap bootstrap.sh writes.
- The four page states through `make_app` + TestClient: dead kubectl → "Could not
  read this cluster", no button, no empty claim; namespace with 0/1 pods ready →
  "namespace gojira exists, nothing answers (0/1 pods ready)" + Install Gojira,
  no version; namespace + ready pods + the real ConfigMap → version + re-run;
  no namespace → empty text + button (your test).
- The install run's one `pods_ready` read after install.sh is sound: Gojira's
  Deployment is `Recreate` (RWO PVC), so there is never a terminating old pod
  beside the new one when `rollout status` returns. I checked prod's namespace:
  one pod. Not an item.

## Fix round (four items, each with a test that fails at 811e21f)

### 0. `KubectlCLI.namespaces()` returns bytes, so every real cluster is "empty"

`clients.py:238-242`: `subprocess.run(... capture_output=True)` without
`text=True` gives `r.stdout` as bytes, so the list is `[b"default", b"gojira", …]`.
`preflight()` then asks `stack.gojira.namespace not in listed`, a `str` against
`bytes`: never found. I ran it, read-only, against the prod install:

```
>>> KubectlCLI(<prod context>, env=...).namespaces()[0]   -> bytes, 41 namespaces
>>> "gojira" in listed                                     -> False
```

and the real `preflight()` on the prod profile:

```
Gojira         | warn | no Gojira on this cluster yet
litellm: Gojira | warn | not installed yet — Install Gojira first
cabal, liminal, bab, kobo: the same
```

That is the one live install, running v1.3.1, reported as having no Gojira. The
shell-fake test could not see it: it only checks the empty case, where bytes and
str give the same answer. `namespace_exists` beside it is fine because it reads
the return code, not stdout.

Fix: `text=True` (and the `!= "NAME"` guard is then a real comparison; with
`--no-headers` it never fires anyway). Test: a kubectl on PATH that lists
`default gojira kube-system` → `preflight()` must NOT add the warn row and must
reach `open_gojira`; and a unit test `namespaces()` returns `list[str]`. Both
fail at 811e21f.

**mypy sees this** (`uvx mypy --ignore-missing-imports stack tests`):
`clients.py:242: error: List comprehension has incompatible type List[bytes];
expected List[str]`. It is the only error in the tree. Put it in `make check`.

### 1. The app-row wording is wrong in the common case

`check_cluster` (preflight.py:98-101) now says
`not installed yet — Install Gojira first` for **every** app without a Gojira
row, whatever cluster it runs against. I called the real `check_cluster` with a
fake cluster that has the namespace and a `FakeGojira(version="v1.3.1")` with no
apps, which is prod the day someone adds a sixth app to the stack file:

```
kobo: Gojira | warn | not installed yet — Install Gojira first
```

Gojira is there. The right action is Install **kobo**, which is the words the
row had before (`not connected; Install does it`). The new sentence belongs only
to the empty-cluster branch.

Fix: `check_cluster` keeps the old wording; the empty branch in `preflight()`
supplies the "Install Gojira first" wording (a parameter, or build those rows in
the `_NoGojira` branch directly). Test: `check_cluster(stack, kc_with_ns,
FakeGojira(apps=[]))` → detail contains `not connected` and not `Install Gojira`.
It fails at 811e21f. No existing test pinned the old wording, which is how this
slipped; now one does.

### 2. `FakeKubectl.namespaces()` cannot be called

`tests/fakes.py:64` sets `self.namespaces: set[str] = set()`; line 86 defines
`def namespaces(self)`. The instance attribute shadows the method:

```
>>> FakeKubectl().namespaces()
TypeError: 'set' object is not callable
```

Nothing calls it (grep: zero callers in tests/), so nothing caught it. The
empty-cluster preflight test uses a shell `kubectl` on PATH instead, which is a
fine second test but means the fake's new method was never exercised. Same bug
class as `FakeGcloud.account` in note 06.

Fix: rename the attribute (`self.ns_set` or `self._namespaces`; update the
`k.namespaces.add(...)` calls in tests) so the method is reachable. Test: one
preflight test that monkeypatches `stack.preflight.KubectlCLI` to return a
`FakeKubectl` with no `gojira` namespace and asserts the warn row — that call
raises `TypeError` at 811e21f.

### 3. STATUS says `/healthz` is read; the code never reads it

STATUS ("Now read where Gojira actually reports it: /healthz primarily") and
your message say the version comes from `/healthz` first. `_gojira_version`
(ui.py:419) takes `gojira=None`, and both callers (ui.py:175 page, ui.py:411
install run) pass none. `GojiraClient.health()` has no production caller and no
test. On the real page and in the real run the version comes from the ConfigMap,
and only from the ConfigMap.

That is a fine source. `GOJIRA_VERSION` in `gojira-install` is the very value
`/healthz` serves (Gojira's config reads it from that ConfigMap). So the fix is
one of two, your pick:

- drop `health()` from the protocol, the fake and the helper, and let STATUS say
  "the gojira-install ConfigMap, which is what /healthz serves"; or
- wire it: after `pods_ready` in the install run, `open_gojira(...)` gives the
  port-forwarded `HttpGojira`; pass it; add a test where the ConfigMap is absent
  and the fake's `health()` carries the version (fails at 811e21f because the
  helper is never given the client).

Either way the rule from 09 stands: STATUS names what the diff does.

## Nits (no test, fix if you are in the file)

- `tests/test_preflight.py::test_an_empty_cluster_is_a_plain_word_and_every_row_runs`
  runs its second half twice (the same stack, the same asserts, pasted). Delete
  the copy.
- `ui.py:394-395` `except ToolError: raise` is a no-op (from 09).
- `ui.py:393` `out` from `_run_install` is never read (from 09).
- The namespace-only note says "nothing answers (1/2 pods ready)" during a
  rollout where one pod answers. "pods not all ready (1/2)" is true in both cases.

## Done when

- Each new test fails at 811e21f (say which, say how).
- I rerun `check_cluster` with an installed fake Gojira and an unconnected app
  and read the old words; I rerun the real `preflight()` on an empty fake through
  `FakeKubectl` (no shell) and read the warn row; STATUS and the code agree on
  where the version comes from.
- `make check` green, CI green, commit this note with your next commit.

— Ember
