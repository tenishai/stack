# stack ui, increment 2: a fresh cluster to a running stack, from the page

Design: tenishai/stack#12. Increment 1 (PR #17) put profiles, Check, Install and
Upgrade behind buttons for a cluster that already has Gojira. Increment 2 is the
rest of the stranger's path: the install guide's steps 1 to 4 and the Secrets a
person still supplies by hand, done from the page, and closes #9.

The sentence this increment is judged by:

> A person with a GCP project they own, a GitHub account, and `stack ui` running
> on their laptop brings an empty cluster to the whole stack running, registered
> in Gojira, without opening a terminal, and without the page ever holding a
> secret.

Each criterion below is checkable. The translator turns them into the e2e plan
(tests/e2e/) from the rendered page, never from the code; the runner reports.

## Criteria

1. **The profile form writes the env file.** The profile page edits the install's
   `gojira.env` (the knobs in `gojira.env.example`, each with its one-line doc):
   kube context as a dropdown read from the profile's kubeconfig, project and
   region from the operator's gcloud, cluster name, GitHub App id and pem path,
   terraform-or-prepare-project as one choice. Saving writes the file in the form
   `scripts/env.sh` loads (one `KEY=value` per line, comments on their own lines).
   Check: the saved file loads through `scripts/env.sh` and `stack check` reads it.
2. **Check tells the truth about an empty cluster.** On a context whose cluster has
   no Gojira, Check says so in plain words ("no Gojira on this cluster yet") and
   the page offers *Install Gojira* instead of a refusal or a traceback. Every
   other row still runs (tools, files, gh, SSO).
3. **The GitHub App is a pause card.** Before Gojira can be installed the page shows
   one card: the link to create the App, the exact settings (from
   docs/github-app-setup.md), two fields (App id, pem path), a *Done* button.
   Done checks, and says which failed: the pem parses as a private key; the App
   answers for that id (JWT → GET /app); the App is installed on the repos the
   stack file names (or on all repos). Nothing is stored but the id and the path,
   in `gojira.env`.
4. **Install Gojira is a run.** One press runs Gojira's `scripts/install.sh` under
   the profile's env (KUBECONFIG, CLOUDSDK_CORE_ACCOUNT, GOJIRA_ENV pinned per
   call), lines live on the run page, a stop in red with the script's own
   sentence. Done means the Gojira pod is ready and `/healthz` answers with a
   version. Pressing again on an installed cluster is idempotent (install.sh
   already is). After it, the stack engine registers the cluster as today.
5. **A missing file is a pause card, not a stop.** A stack-file source `file:<path>`
   whose file is absent (kobo's App pem, a config template) pauses the run with a
   card naming the path and what the file is for; Done re-checks and continues.
   The page never shows the file's contents and never copies it anywhere but the
   cluster Secret the stack file names.
6. **Install's done is the rolled deployment.** Install waits for the run that the
   merged setup PR starts and reports done only when the pods run the new digest
   (as Upgrade does today; the 2026-11-10 kobo memory change said "done" while the
   old pods were still up). A failed post-merge run is a stop with the Actions URL.
7. **A run survives the operator.** Closing the browser, or `stack ui` itself,
   mid-run loses nothing a second press cannot continue: the engine is idempotent,
   the run page shows what completed, and pressing Install again picks up where
   the cluster is.
8. **The CLI and the page stay interchangeable.** The same profile, stack file and
   env file drive `stack install` and `stack upgrade` from a terminal with the
   same result. Nothing the page needs lives only in the page.
9. **No secret material anywhere the page writes.** `~/.config/stack/profiles.yaml`,
   the env file, run lines, logs and the e2e report hold paths and ids, never a
   key, token or pem body. Check: a grep over all of them after a full run finds
   none of the generated values.
10. **The stranger run passes.** The e2e plan runs twice: on a kind cluster for the
    page's mechanics (criteria 1 to 3, 5, 7, 9; Gojira's own install cannot run on
    kind), and once by a person on a throwaway GCP project with the page alone
    (criteria 4, 6, 8, 10). The person's run is the acceptance; the kind run is
    the regression test.

## Out of scope (still)

Org-config repos, storing secrets, serving beyond localhost, the container image
for the installer (follows this increment).
