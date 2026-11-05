# stack

Install the tenish stack onto a running [Gojira](https://github.com/tenishai/gojira):
one file, one command, and the merges stay with a person.

```
make install            # in gojira: the platform itself (terraform + bootstrap)
stack install stack.yaml
```

The second command registers the cluster, connects each app in the file's
order, waits for Gojira to provision it, applies the cluster objects and
ConfigMaps the app needs, creates its Secrets from the sources the file names,
waits for a person to merge the setup PR (or merges it, when told to), deploys
when a reconnect came back empty, and waits for the pods. When an app is the
switchboard (cabal), it catches the one-time bootstrap code from the pod's log,
logs in, and mints the tokens the later apps need.

Run it twice and the second run finds everything present and says so.

## Why

Bringing the stack up by hand on a fresh install took about 25 touches across
two people (2026-10-08): six Secrets, three merges, a ClusterRole, a ConfigMap,
four connects, a login inside a five-minute window, two minted tokens, a CI
dispatch, three syncs. Half were sequencing a stranger shouldn't have to know
(kobo's ClusterRole before kobo; cabal before its tokens; tokens before liminal
and kobo). This file is that sequence written down.

## The file

See [examples/dogfood.yaml](examples/dogfood.yaml). Apps are listed in
dependency order. Every Secret and ConfigMap value is a *source*, never a value:

| Source | Meaning |
| --- | --- |
| `file:<path>` | the bytes of a file (trailing newline stripped); a relative path is relative to the stack file |
| `generate:<n>[:<name>]` | n random bytes, base64, made once and kept in `.generated/` (mode 600) beside the file; a named value is one value for every source in the stack that names it |
| `gojira-token` | the install's own API token, read from Gojira's Secret |
| `template:<path>` | a file with `{gojira_token}` filled in; relative like `file:` |
| `cabal-token:<sub>:<scope>` | a 90-day service JWT minted on the cabal this run brought up |
| `literal:<text>` | the text itself; for names, never for secrets |

An app with `cluster_objects` names files in its repo (cloned under `--repos`)
that an operator applies once: a ClusterRole Gojira then binds through
`cluster_roles`. `bootstrap: cabal` marks the switchboard. An app with
`auto_merge_setup_pr: true`, or a run with `--merge-setup-prs`, merges its own
setup PR; otherwise the run prints the link and waits.

## Software you did not write

An app can be a published image instead of a repo to build (Gojira's `image:`,
tenishai/gojira#108). The entry names the upstream repo (cloned under
`--repos`, for its `k8s/base` and any cluster objects), the image, and a repo
of your own for the installer to write and connect:

```yaml
  - name: cabal
    repo: tenishai/cabal                       # the upstream: its k8s/base travels
    image: us-central1-docker.pkg.dev/tenish/images/cabal:0.3.2
    deploy_repo: acme/cabal-deploy             # created if missing; gojira.yaml + k8s/base, nothing else
    contract:                                  # merged last into that gojira.yaml: the install's facts
      env: { VERTEX_PROJECT: acme-prod }
      identity: { gcp_roles: [roles/aiplatform.user] }
```

The upstream's `build_args`, `arch` and `node_selector` are left behind: nothing
is built, and the platform's architecture is the install's. The deploy repo is
pushed directly (it exists for exactly this content); a rerun with identical
content pushes nothing. Your deploy identities must be able to read the image's
registry; a private Artifact Registry wants `roles/artifactregistry.reader`.

## Upgrading

`stack upgrade stack.yaml [app …]` converges a changed `image:` onto a running
install (tenishai/stack#12). Bump the tag in the stack file and run it: for
each image app whose deploy repo pins a different digest, the run copies the
image from `source_image:` into the install registry when it isn't there yet
(crane, no docker daemon), lands the one-line digest pin on the deploy repo,
presses provision, lets the regenerated setup PR settle, and is done only when
the deployment runs the new digest with every pod ready — the receipt, not a
green run. An app already at its digest says so and is skipped.

```yaml
  - name: kobo
    repo: tenishai/kobo
    image: us-east4-docker.pkg.dev/acme-prod/agent-images/kobo:v0.3.54
    source_image: ghcr.io/tenishai/kobo         # where releases are published; the tag comes from image:
    deploy_repo: acme/infra-kobo-deploy
```

How the pin lands is the file's `upgrade_gate`: `pr` (the default) opens a
pull request and waits for a person's merge — the review; `push` lands it
directly, and your running the command is the gate. An app that carries a
`data_mount` is refused without `--yes`: back up the PVC first. The pin
push's own CI run fails the contract check by design (the workflow still
carries the old contract sha); the provision that follows regenerates it.

## What it needs

`kubectl` on the install's context, `gh` logged in with access to the repos,
for a stack with cabal, a `cbl` binary (`--cbl`, from cabal's own venv), and
for `upgrade`, `crane` (its registry auth comes from your docker credential
helpers: `gcloud auth configure-docker <host>` covers Artifact Registry).
Gojira is reached through a port-forward of its Service unless `gojira.url`
is set. Before touching anything the run names the kubectl context it will
use and refuses one without Gojira's namespace; `gojira.kube_context` pins
the context (the dogfood file does), and every kubectl call answers or fails
within seconds rather than waiting on a cluster that is not there. The dogfood file brings exactly one thing from your machine: the
GitHub App's private key (`~/tenish-room.pem`); its config files travel with
it under `examples/dogfood/` and its keys are generated on the first run.

## What it refuses

To merge a PR on a repo you own unless you said so; to touch anything the file
does not name; to write a Secret value anywhere but the cluster (generated
keys are the one exception, kept in a 600 file so a rerun does not rotate a
key a running app already holds).

## Development

```
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check stack tests
```

The tests run the installer against fakes of the four hands (Gojira's API,
kubectl, gh, cbl), including the checked-in example end to end.
