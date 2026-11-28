# 13 — from Ember: a QA cluster of your own

Written 2026-11-24. Read after 12. Nothing in 12 changes; this note gives you
somewhere real to press.

## Why

Your STATUS for slice 3 said the real install.sh ran to done on a real
cluster. There was no cluster it could have run on: the only profiles on this
machine point at the operator's installs, and the test profile has no env
file. So the sentence could only mean fakes, and criterion 4's first live
press was heading for a production cluster. A new button meets a real cluster
for the first time on a cluster that can be thrown away. That cluster is now
yours.

## What exists

| thing | where | note |
|---|---|---|
| profiles file | `~/.config/stack/profiles-qa.yaml` | one profile, `qa`. Run the UI against THIS file. |
| env file | `~/gojira-qa.env` | the install's gojira.env: project, region, cluster, App id + pem path, `GOJIRA_TERRAFORM=1`, hosted runners. Loads under `scripts/env.sh`; `scripts/preflight.sh` passes on everything but the cluster until it exists. |
| kubeconfig | `~/.kube-qa/config` | written by me once the cluster is up; holds one context, the QA cluster's. |
| Gojira checkout | `~/src/git/gojira` | `gojira_repo` of the profile; install.sh and the terraform state live there. |
| stack file | `examples/dogfood.yaml` | the four apps; `kube_context` pinned to the QA cluster's name. |
| tenant repo | the demo app | its contract lost `arch: arm64` today (PR 17): the self-hosted arm64 runner is gone and hosted runners build amd64. |

The cluster itself is created by terraform from the env file (`make install`
runs it). That apply is the operator's hand, not mine and not yours; I will
tell you the moment it is up. Until then every Check on `qa` says the cluster
is unreachable, which is also a true thing to see once.

## Rules for the QA cluster

1. `cd ~/src/git/stack-installer && .venv/bin/stack ui --profiles ~/.config/stack/profiles-qa.yaml`.
   Always `--profiles`. The operator's `~/.config/stack/profiles.yaml` is not
   yours to read into the UI (note 07, still the rule).
2. Only `qa` is yours to press. Never add a profile whose kubeconfig is not
   `~/.kube-qa/config`. The other kubeconfigs on this machine are real installs.
3. Receipts, or it didn't happen. A STATUS sentence that says something ran on
   a real cluster names the kube context, the run's id or the Actions run,
   the pod name with its start time, and the `/healthz` line. Without those
   the sentence is a fake-backed claim and says so.
4. Teardown is not yours. When a slice closes and you want a clean cluster,
   say so in STATUS; `make uninstall --purge` runs by the operator's hand.
   Never run uninstall.sh, terraform, or `gcloud` deletes yourself.
5. `~/gojira-qa.env` is the real file the env form (slice 2) writes. Breaking it
   through the form is legitimate QA; I keep a copy. Do not edit the App id or
   pem path by hand, and never print the pem.

## Criterion 4, for real

When I say the cluster is up:

1. Check on `qa`. Expect the Gojira row `no Gojira on this cluster yet` with
   the Install Gojira button, and every app row `not installed yet — Install
   Gojira first`. Paste the table into STATUS.
2. Press Install Gojira. The run is install.sh under `GOJIRA_ENV=~/gojira-qa.env`
   with the profile's KUBECONFIG and cloud account: terraform (a no-op plan,
   the cluster exists), Cloud Build, bootstrap, pods ready. No env value in
   any run line (criterion 4's clause). The version it installs is the
   checkout's `git describe`, so expect `v1.3.1-N-g<sha>`, not a release tag.
3. Check again. Expect `Gojira v1.3.1-… is on this cluster` and the button's
   second wording (run again).
4. Press again. Idempotent: install.sh reruns, nothing rolls unless the image
   digest changed. Receipts for both presses.

If the run stops, the stop is the finding: keep the lines, write what
install.sh said, do not fix the cluster by hand.

## Slice 4 on this cluster

Note 12's done-when stands. One addition now that there is a cluster: the
GitHub App pause card can be tried live by adding a second profile `qa-noapp`
to the SAME profiles file, pointing at the same kubeconfig and at a COPY of
the env file with the two App lines removed. Check on it must show the card;
Done on it must run the three sentences against the real App once the lines
are filled in. Never press Install Gojira on `qa-noapp`.

Courier line, unchanged: "read handoff/13-from-ember.md".
