# Handoff 01: stack ui increment 2 (for Q)

You build; Ember writes the acceptance and validates; Shaz judges. Read, in this
order: README.md, docs/ui-increment-2-acceptance.md (the criteria), stack/ui.py,
stack/runs.py, stack/steps.py, stack/profiles.py, stack/preflight.py; then in
~/src/git/gojira: gojira.env.example, scripts/env.sh, scripts/install.sh,
scripts/preflight.sh, docs/github-app-setup.md. Do not read tests/e2e/ to decide
what to build; it is the translator's, written from the rendered page.

Rules of the road: tests first per slice (pytest, fakes in stack/ and tests/;
no live cluster in unit tests); `make check` green before each commit; commit
small, sign `-s`; STATUS.md at the repo root says what is done, what is next,
and what is uncertain, updated with every commit; never point a test or a
fixture at the prod profile; never print or log a value that came from a
`file:` or `generate:` source. Port 8097 is the page.

Slices, in order. Each is one or more commits with its tests; stop after each
and write STATUS.md so Ember can validate before the next.

1. **Warm-up: Install waits for the roll** (criterion 6). After the setup PR
   merges, wait for the run it starts and for the pods to run the merged digest,
   the way `upgrade`'s `wait_rolled` already does; a non-success post-merge run
   is a ToolError carrying the Actions URL. Fake-kubectl + fake-gh tests: done
   only after the digest flips; the failed run stops with the URL.
2. **The env file as a form** (criterion 1). A `stack/envfile.py` that reads and
   writes `gojira.env` losslessly (comments on their own lines, order kept,
   unknown keys kept); the profile page edits the known knobs with their one-line
   docs from `gojira.env.example`; kube contexts listed from the kubeconfig,
   projects from `gcloud projects list` under the profile's account. Tests: a
   round-trip keeps every comment; a saved file loads under a copy of
   `scripts/env.sh` (subprocess, bash); a trailing comment on a value is refused
   the way env.sh refuses it.
3. **Check on an empty cluster + Install Gojira** (criteria 2, 4). Check's
   cluster row says "no Gojira on this cluster yet" (today's code treats it as
   an error); the page then shows *Install Gojira*, which runs
   `scripts/install.sh` from the profile's Gojira checkout (a new profile path,
   `gojira_repo`) with the env pinned, as a run. Done = pod ready + `/healthz`
   version. Tests on a fake install.sh (a script that prints lines and exits 0 or
   1) and a fake kubectl.
4. **The GitHub App pause card** (criterion 3). Shown when the env file has no
   App id or pem, or the pem does not parse; Done verifies via a JWT (`PyJWT`
   is already a dependency of gojira, check ours) against a `GitHubClient`
   method `app_identity(app_id, pem_path)` and `installation_covers(repos)`.
   Fakes for both; the card's three failure sentences each pinned by a test.
5. **Missing-file pause cards** (criterion 5). `file:` sources are checked
   before the run starts and again at the step that reads them; a missing one
   pauses the run (a new run state `paused`, with the card's text) instead of
   stopping it; Done resumes. Tests: a run pauses, Done after the file appears
   continues, the file's bytes never appear in run lines.
6. **Survival** (criterion 7). Runs persist their lines to
   `~/.config/stack/runs/<id>.jsonl` so a restarted `stack ui` shows them;
   a second press on a half-done install continues. Tests on a tmp profiles dir.

Questions for Ember go in handoff/NN-from-q.md; answers come back as
handoff/NN-from-ember.md. Ember validates each slice with its own run plus
negative checks (a test that should fail against the previous commit) before
the next slice starts.
