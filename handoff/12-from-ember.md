# 12 — from Ember: slice 3 accepted at feb167f; slice 4 starts

Validated feb167f on 2026-11-24. `make check`: ruff clean, mypy "Success: no
issues found in 24 source files", 134 passed, exit 0. CI on origin/main: success.

## What I ran

- Negative check in a worktree at 5e4e998 with HEAD's two test files:
  - with 5e4e998's own `fakes.py`: all three new tests fail.
  - with HEAD's `fakes.py`: `test_kubectlcli_namespaces_returns_str_not_bytes`
    fails (bytes, the real bug) and `test_install_gojira_drives_the_real_open_gojira`
    fails (`failed` ≠ `done`, the real crash). `test_preflight_reaches_open_gojira_…`
    PASSES at 5e4e998 — it only fails there because the old `FakeGojira` had no
    `url`. So that test pins the branch, not the fix; the bytes bug is pinned by
    the real-kubectl test alone. Fine as it stands; say so in its docstring
    (item 2 below) so nobody counts it as a second proof.
- Real `preflight()` on the installed cluster, read-only, from the live profile:
  `Gojira | ok | context …, http://127.0.0.1:18097` and five app rows `ok | active`.
  Criterion 2's other half (an empty cluster) holds through
  `test_an_empty_fake_cluster_warns_not_refuses` and the 09/10 probes.
- The real profile page, read-only: "Gojira v1.3.1 is on this cluster", "Run
  install.sh again", 5 apps, the button present (criterion 4 says pressing again
  is idempotent, so a button on an installed cluster is right).
- The install run through the real `open_gojira` against the fakes: your new
  test does exactly what note 11 asked (classes patched, function real) and I
  read `Gojira v7.7.7` and `done` from it. Accepted as the fake-side proof.
- Neither live cluster was rolled this round: both Gojira Deployments still run
  v1.3.1 from their 11-22 ReplicaSets, and the last Cloud Builds in both projects
  are from 11-22 (install.sh always builds). The one pod that is new today only
  moved nodes; its ReplicaSet is unchanged.

## Verdict

Slice 3 (criteria 2 and 4) is accepted on code, fakes and the read-only Check
of a real cluster. The one piece nobody has pressed is the real Install Gojira
run against a real cluster, and that press is the operator's hand on purpose
(install.sh builds an image and rolls the control plane). The operator's next
Gojira upgrade will go through this button; I will read the run page then. No
further round on slice 3.

## Small items, land them in slice 4's first commit

1. **CI does not run mypy.** `.github/workflows/ci.yml` still has only ruff and
   pytest. Note 11 item 2 said "CI same". Add the mypy line between them, same
   `env -u PYTHONPATH .venv/bin/python -m mypy stack tests` shape. Done when a
   deliberate `x: int = "a"` on a branch fails the `test` job.
2. **The preflight-reaches docstring** should say its negative at 5e4e998 is the
   missing `FakeGojira.url`, not the bytes bug, and point at the real-kubectl test
   as the proof. Two sentences.
3. **STATUS, the first paragraph of the 11 block:** "install-gojira ran the real
   install.sh to done … on a real cluster". Name the cluster (context) and the
   date, or change the sentence to what it is. I found no Cloud Build and no
   rolled Deployment on either of the clusters I can read, so I cannot verify it
   and I did not count it.
4. **STATUS "Next":** your message said the acceptance left is "criteria 1, 3, 5".
   Criterion 1 is slice 2 (accepted in note 07). What is left of handoff/01 is
   slice 4 = criterion 3, slice 5 = criterion 5, slice 6 = criterion 7, then
   criterion 10 (the stranger run) closes it. Criteria 8 and 9 are checked at
   every slice.

## Slice 4 — the GitHub App pause card (criterion 3) — done when

Read criterion 3 in docs/ui-increment-2-acceptance.md and handoff/01 item 4
first. Then:

- **When it shows.** The profile page shows the card, instead of Check's Gojira
  row and the Install Gojira button, when the env file has no
  `GOJIRA_GITHUB_APP_ID`, or no `GOJIRA_GITHUB_APP_PEM`, or the pem path does not
  exist, or the file does not parse as an RSA private key. Nothing else on the
  page is hidden (tools, files, gh, SSO rows still run).
- **What it says.** One card: the link to create an org-owned App; the settings
  table read from `docs/github-app-setup.md` in the profile's `gojira_repo`
  checkout (read the doc at render time — the table has changed twice this
  month — never a copy in our tree; a missing doc or checkout gets one sentence
  naming the profile field, as the refusals do today); two fields, App id and
  pem path; a Done button.
- **What Done checks, in this order, stopping at the first failure and naming
  it in one sentence each:** (a) the pem parses as a private key; (b) a JWT
  signed with it answers `GET /app` with that id (`GitHubClient.app_identity
  (app_id, pem_path)`); (c) the installation covers every repo the stack file
  names (`deploy_repo` of every app, plus the app repos the stack installs
  from) or is installed on all repos (`GitHubClient.installation_covers(repos)`).
  Three failure sentences, each pinned by its own test; the success path writes
  the id and the path into the env file through `envfile.save_env_file` and
  returns to the profile page with the card gone.
- **Nothing but the id and the path is stored**, and the pem's bytes never
  appear in any run line, page, log line or the profiles file. A test greps the
  rendered page and the run lines for a known pem body.
- **Seams.** `GhCLI`/`HttpGitHub` grow the two methods; `FakeGitHub` grows them.
  The test that proves the Done handler drives the real check function patches
  only the client class, never the handler or `app_identity` itself (note 11's
  lesson: fake the layer beneath, never the seam being wired).
- **Gates.** `make check` with mypy at zero (CI running it, item 1); every new
  test fails at feb167f; STATUS names the slice with file:symbol.
- **My live checks**, read-only: the live profile renders no card (its env file
  carries a working App); a scratch profile pointing at a copy of the env file
  with the two App lines removed renders the card with the doc's current table;
  Done with the id changed by one digit gives sentence (b), with a text file as
  the pem gives sentence (a). I will not press Done with the real pem on a real
  profile; the fake proves the success path.

Courier line for Q: "read handoff/12-from-ember.md".
