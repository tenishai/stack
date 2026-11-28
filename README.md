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

## Quickstart

```bash
git clone https://github.com/tenishai/stack && cd stack
uv venv .venv && uv sync --locked --extra ui
.venv/bin/stack ui          # localhost:8097: a profile, four things, the buttons
```

Nothing in `examples/stack.yaml` is edited: it carries `{org}`, `{project}`,
`{region}` and `{cluster}`, and the profile fills them. The same from a terminal:

```bash
.venv/bin/stack env examples/stack.yaml --env-file gojira.env --gojira-repo ../gojira \
    --set org=<your org> --set project=<your project> --set region=us-central1 --set cluster=gojira-sandbox
.venv/bin/stack install examples/stack.yaml --merge-setup-prs --cbl <path to cabal's cbl> \
    --env-file gojira.env --set org=… --set project=… --set region=… --set cluster=…
```

[docs/install.md](docs/install.md) is the whole path, from an empty project
through Gojira to the room, with the pauses that are yours. The stack file
names published images; nothing is built and nothing is cloned. It needs a
Gojira already installed and reachable from your current kube context, `gh`
logged in to your org, `gcloud` on the project, and the one file you bring: the
GitHub App's private key. Rerunning is safe: every step reads before it writes.
`stack ui` puts the same steps behind buttons on `localhost:8097`.

`examples/dogfood.yaml` is our own install: the same apps built from their
source repos on our cluster. Read it for the shape; run the one above.

## Why

Bringing the stack up by hand on a fresh install took about 25 touches across
two people (2026-10-08): six Secrets, three merges, a ClusterRole, a ConfigMap,
four connects, a login inside a five-minute window, two minted tokens, a CI
dispatch, three syncs. Half were sequencing a stranger shouldn't have to know
(kobo's ClusterRole before kobo; cabal before its tokens; tokens before liminal
and kobo). This file is that sequence written down.

## The file

See [examples/stack.yaml](examples/stack.yaml) (published images) and
[examples/dogfood.yaml](examples/dogfood.yaml) (our build-from-source install). Apps are listed in
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
tenishai/gojira#108). The entry names the upstream repo (for its `k8s/base` and
any cluster objects, read from a clone under `--repos` when there is one and
from GitHub when there is not), the image, and a repo of your own for the
installer to write and connect:

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

Files only your install knows about, such as an ExternalSecret naming your own
vault path, go in `base_files:` (paths relative to the stack file). The
installer writes each one into the deploy repo's `k8s/base` and lists it in
that directory's kustomization; Gojira keeps only resources a kustomization
names, so a file that isn't listed would sit in the repo and leave the build.
A base file may not share a name with one of the upstream's own.

```yaml
    base_files: [prod/kobo/externalsecret-okta.yaml]
```

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

## Buttons

```
pip install 'tenish-stack[ui]'
stack ui            # http://127.0.0.1:8097
```

The same files behind buttons (stack#12). A **profile** is one install: a
name and paths — the stack file, and the kubeconfig and cloud account every
call is pinned to, so nothing depends on a shell export (the wrong-context
class of accident lives entirely in the shell). The list is
`~/.config/stack/profiles.yaml`, or `$STACK_PROFILES`; it holds paths, never
contents. A profile's page shows what its stack file says and three buttons:

- **Check** — preflight. Tools on PATH, every file the stack file names,
  whether gh is logged in and its token sees the repos' orgs (a token with
  no SSO grant lists nothing and says nothing), the kube context, and each
  app's state on Gojira: its row, its pods, what it runs against what the
  file pins. Each row names what is missing and whose hands fix it; the page
  runs under your credentials and can grant nothing.
- **Releases** — for an app with `source_image:`, the app table lists the
  version tags published there, newest first. Pick one and press *Upgrade
  to*: the page rewrites that app's `image:` line in the stack file to
  `<repo>:<tag>@<digest>` (the digest read from the source; the one line a
  person would have edited, the rest of the file untouched) and runs the
  upgrade for that app. An app without `source_image:` says so in its row.
- **Install** / **Upgrade** — the command line's engine, on the apps you
  tick (Install with none ticked is every app; Upgrade needs its apps
  named). Pressing is the review: the PRs a run opens on the install's own
  repos (setup PRs, pins) are merged by the run and linked in its lines, so
  the only gate left is the backup box for an app with a `data_mount`. The
  run's lines appear as they happen; a stop is the same sentence the terminal
  prints, in red. One run at a time, on localhost only; runs live while
  `stack ui` does.

The start-to-finish install, from an empty project through Gojira to the room,
is [docs/install.md](docs/install.md).

## What it needs

`kubectl` on the install's context, `gh` logged in with access to the repos,
for a stack with cabal, a `cbl` binary (`--cbl`, from cabal's own venv), and
for `upgrade`, `crane` (its registry auth comes from your docker credential
helpers: `gcloud auth configure-docker <host>` covers Artifact Registry).
Gojira is reached through a port-forward of its Service unless `gojira.url`
is set. Before touching anything the run names the kubectl context it will
use and refuses one without Gojira's namespace; `gojira.kube_context` pins
the context (the dogfood file does), and every kubectl call answers or fails
within seconds rather than waiting on a cluster that is not there. A stack file
brings exactly one thing from your machine: the GitHub App's private key; its
config files travel with it (`examples/stack/`, `examples/dogfood/`) and its
keys are generated on the first run. `--repos` is optional: an upstream's
`k8s/base` and cluster objects are read from GitHub when no clone is there.

## Runs outlive the page

Every run is also a file under `runs/` beside the profiles file
(`~/.config/stack/runs/<id>.jsonl`, mode 600): its lines as they were said, and
how it ended. A restarted `stack ui` lists them all. A run the UI stopped under
shows as failed with one line saying so; press again and the engine, which
reads before it writes, continues where the cluster is. The files hold paths
and ids, never a key or a token: nothing the run says is secret.

## What it refuses

To merge a PR on a repo you own unless you said so; to touch anything the file
does not name; to write a Secret value anywhere but the cluster (generated
keys are the one exception, kept in a 600 file so a rerun does not rotate a
key a running app already holds).

## Development

```
uv venv .venv && uv sync --locked   # the lock is the pin; --locked refuses a stale one
.venv/bin/python -m pytest -q
.venv/bin/ruff check stack tests
```

The tests run the installer against fakes of the four hands (Gojira's API,
kubectl, gh, cbl), including the checked-in example end to end.

## Community

[CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
[SUPPORT.md](SUPPORT.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md),
[ARCHITECTURE.md](ARCHITECTURE.md).

## License

[MIT](LICENSE). Copyright (c) 2026 tenish and the stack contributors.
