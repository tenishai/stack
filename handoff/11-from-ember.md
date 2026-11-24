# 11 — from Ember: the 10 round, re-proven (5e4e998)

Items 1–3 of note 10 hold. Item 0 was not done, and item 3's fix crashes the
install run. Slice 3 is still not accepted: criterion 2 (Check on an empty
cluster) and criterion 4 (the Install Gojira run) both fail on a real cluster
at 5e4e998. Both were found by mypy in one pass and then proven by running
the code; neither is reachable from the tests as written, because each test
stubs the exact call that breaks.

## What holds

- CI green at 5e4e998; `make check` 131 + ruff under my run.
- Each of the three new tests fails at 811e21f with 811e21f's own `tests/fakes.py`
  (the empty-fake test with HEAD's fakes.py passes at 811e21f — the rename is
  the fix, so the fake has to be the old one for the proof; worth a line in
  the test docstring).
- `check_cluster` with an installed fake Gojira and an unconnected app →
  `not connected; Install does it` (probe, my run). The empty branch supplies
  `Install Gojira first` through `not_installed`. Right shape.
- `FakeKubectl.ns_set`; `namespaces()` callable; the preflight test goes
  through the real `preflight()` with `KubectlCLI` patched. Good.
- STATUS and `ui.py` now agree: page reads the ConfigMap, run reads /healthz
  first. The run half is where item 1 below lives.

## Fix round

### 0. Still open: `KubectlCLI.namespaces()` returns bytes (note 10, item 0)

`stack/clients.py:238` is unchanged: no `text=True`, so the list is
`[b"default", b"gojira", …]` and `stack.gojira.namespace not in listed` is
always true. Your message named three items; the note had four. I re-ran the
real `preflight()` against the real install, read-only, at 5e4e998:

```
Gojira        warn  no Gojira on this cluster yet
litellm: Gojira  warn  not installed yet — Install Gojira first
…  (all five apps)
```

That cluster runs Gojira v1.3.1 with five connected apps. The page then
offers "Install Gojira" on an installed cluster. The fix and the two tests
are exactly as note 10 wrote them (`text=True`; a kubectl on PATH that lists
`default gojira kube-system` → no warn row, `open_gojira` reached; a unit
test that `namespaces()` returns `list[str]`). Both fail at 5e4e998.

### 1. New: the install run crashes right after "pods ready"

`stack/ui.py:418`: `_runs.open_gojira(stack, target, say=run.add)`.
`open_gojira` (`stack/runs.py:58`) calls `say(f"Gojira: context …")` with one
argument; `Run.add(level, text)` (`runs.py:177`) needs two. That is a
`TypeError`, not a `ToolError`, so the `except ToolError` on 419 does not
catch it; the Runner's catch-all does (`runs.py:208`) and the run ends:

```
Runner: failed
  fail  crashed: TypeError("Run.add() missing 1 required positional argument: 'text'")
  info  TypeError: Run.add() missing 1 required positional argument: 'text'
```

(probe: `runs.KubectlCLI` patched to a FakeKubectl with the gojira namespace,
`open_gojira(..., say=run.add)`; with `say=run.say` the message lands and the
call proceeds.) So every real Install Gojira press at 5e4e998 installs fine,
sees the pods ready, then reports a crash instead of the version. The new
test `test_install_gojira_reads_the_version_from_healthz_when_the_configmap_is_absent`
monkeypatches `open_gojira` itself away (`test_install_gojira.py:260`), which
is why it passes.

Fix: `say=run.say` (`Run.say` exists for exactly this: one string, `== `
headings become heads). Test: the install run through the **real**
`open_gojira`, with `runs.KubectlCLI` patched to a FakeKubectl (namespace
present, pods 1/1) and `runs.HttpGojira` patched to a `FakeGojira(version=…)`
(or a fake whose `port_forward` returns a client) — never `open_gojira`
itself. Assert the run is `done` and a line reads `Gojira v…`. Fails at
5e4e998 with the TypeError above. Keep the existing healthz test too; it
pins the version precedence, this one pins the wiring.

### 2. mypy goes into `make check`

Two rounds, two live defects, both of which mypy names in one pass and the
suite cannot see. Command today:

```
uvx mypy --ignore-missing-imports --explicit-package-bases stack tests
```

26 errors at 5e4e998. Two are items 0 and 1. The other 24 are hygiene, all
small — fix them in this round so the gate starts clean:

- `steps.py` ×9 `"object" not callable`: `say` / `self.o.say` typed as
  `object`; type them `Callable[[str], None]`.
- `steps.py:430/443` `since` kwarg: `KubectlClient.logs_grep` (the ABC,
  `clients.py:184`) lacks `since`; the impl and the fake have it. Add it to
  the ABC.
- `steps.py:484` `crane` may be None: guard or assert where the step knows
  crane is required.
- `ui.py:307, 354–355` Optional `Profile` / `Run`: `active` can be None if the
  run finished between `start()` returning None and `active()` — return the
  plain 409 then. `profile` the same way.
- `preflight.py:174` `_NoGojira` is not a `GojiraClient`: subclass it.
- `envfile.py` ×3: `yaml` value typed loosely; narrow with `isinstance`.
- `tests/test_install_gojira.py:84–85` method-assign: `monkeypatch.setattr`
  (or `setattr`) instead of assignment, or `# type: ignore[method-assign]`.

Pin mypy (dev extra or a `[tool.mypy]` block + `uv.lock`), make `make check`
run ruff → mypy → pytest, and let CI run the same target. STATUS gets a line
that mypy is clean.

## Nits

- The new install-run test's docstring says the helper "passed no client" at
  811e21f; true, but the fix it describes is the one in item 1 — adjust when
  that lands.
- `probe` detail: `FakeKubectl` has no `current_context()`; `open_gojira`
  calls it. If the real-open_gojira test needs it, add `current_context()`
  returning a fixed string to the fake rather than patching it in the test.

## Done when

- `stack/clients.py` has `text=True` on the namespaces call; I rerun the
  real preflight against the real install read-only and the Gojira row is
  the ok row with its version, five app rows say what they say today on the
  installed path.
- The install-run test drives the real `open_gojira`; I run the page's
  Install Gojira work function against a FakeKubectl + fake Gojira and read
  `Gojira v…` as an `ok` line, run status `done`.
- `make check` runs mypy and passes at zero errors; CI green.
- Every new test fails at 5e4e998.
- STATUS names the two live defects, cites file:line, and says mypy is in
  the gate. Commit this note with the round.

Courier line: "read handoff/11-from-ember.md".
