# stack — architecture card

**What it is.** One file (`prod.yaml`, `examples/dogfood.yaml`) names the apps a
Gojira should run, in order, with their images, deploy repos, contracts and the
cluster objects and Secrets each needs. One command installs or upgrades them
through that Gojira, idempotently: every step reads before it writes and reports
`exists` when there is nothing to do. The merges stay with a person unless the
file says otherwise.

**Entry points.**
- `stack install <file> [app…]`, `stack upgrade <file> [app…]` — `stack/cli.py`
- `stack ui` — `stack/ui.py`: profiles, Check, Install/Upgrade with live lines, a
  per-app release picker; the same steps behind buttons.

**Seams.**
- `stack/model.py` — the stack file's schema (`Stack`, `App`, sources such as
  `file:`, `generate:`, `gojira-token`, `cabal-token:`). Validate a file with
  `Stack.load` before anything else.
- `stack/steps.py` — the install and upgrade steps: register the cluster, write a
  deploy repo for an `image:` app, connect, wait for the setup PR and the run,
  create Secrets from the named sources, mint cabal tokens, dispatch, settle.
  `upgrade` mirrors an image (crane), lands the pin (PR or push per
  `upgrade_gate`), waits for the roll.
- `stack/clients.py` — the four hands behind interfaces: `GojiraAPI`, `Kubectl`,
  `GhCLI`, `CblCLI` (opens its own port-forward on demand), plus `CraneCLI`.
  `tests/fakes.py` stands in for all of them.
- `stack/preflight.py` — tools present, files present, `gh` login and SSO per
  org, kube context reaches a Gojira, per-app Gojira row / pods / digest vs pin.
- `stack/templates/` — the UI's HTML.

**Invariants.** Re-running is safe. Secret values are read from the files the stack
file names and written to the cluster only; generated keys are kept in a 600 file so
a rerun does not rotate a key a running app holds. A kube context without a Gojira
is refused in seconds.

**History.** Born 2026-10-08 after the dogfood took ~25 hand touches to assemble;
proven by a stranger's install on 2026-10-13; `upgrade` 2026-11-05; UI 2026-11-09.
