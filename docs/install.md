# Installing the stack

From an empty Google Cloud project to a room that makes agents, in order. Every
step says what it needs from you and what it leaves behind. Most of it is one
command; the pauses are where only you can act (a GitHub App, an approval).
The file you drive it with is [examples/stack.yaml](../examples/stack.yaml):
every app from a published image, each with a small deploy repo of yours.

## What you end up with

| On the cluster | What it is | Where it came from |
|---|---|---|
| Gojira | the deploy platform: it takes a repo (or an image) and runs it with an identity, a namespace, probes, sizing | [tenishai/gojira](https://github.com/tenishai/gojira), installed by its own `make install` |
| litellm | the model proxy in front of Vertex AI | `ghcr.io/tenishai/litellm-deploy` |
| cabal | the switchboard: agents and the room reach each other through it | `ghcr.io/tenishai/cabal` |
| kobo | the room: a web UI where people make and talk to agents | `ghcr.io/tenishai/kobo` |

Each app gets a repo of yours, `acme/infra-<app>-deploy`, holding only a
`gojira.yaml` and the upstream's `k8s/base`. The installer writes it, Gojira
connects it, and its CI copies the published image into your project's own
registry and applies the manifests. Nothing is built.

liminal, the tool gateway, is not in the file yet: tenishai/liminal publishes
wheels, not an image. The room runs without it; agents have no vendor tools
until it joins.

## Before you start

| You need | Notes |
|---|---|
| A GCP project with billing, and Owner on it | A throwaway. Gojira's identity gets broad IAM in it, and so does the room's. Fine for a sandbox and nowhere else. |
| A GitHub organization you administer | The deploy repos, kobo's config repo and every agent repo are created there. |
| `gcloud`, `kubectl`, `gh`, `terraform`, `uv`, `crane` | `gcloud components install gke-gcloud-auth-plugin` too. `crane` copies images (`brew install crane`). No Docker. |
| `gh` logged in | `gh auth login`; the account must be able to create repos in the org. |
| cabal's `cbl` | The switchboard's CLI, from a checkout of tenishai/cabal: `uv venv .venv && uv sync` there, then `.venv/bin/cbl`. The installer logs in to cabal with it once and mints the tokens the other apps hold. |
| About an hour | Gojira's install is sixteen minutes of it; the rest is reading. |

> More than one Google account on this machine? `kubectl` caches its token per
> cluster, not per account. Give this install its own kubeconfig:
> `export KUBECONFIG=~/.kube-acme/config`, and keep it for every command here.

## 1. Gojira

Follow Gojira's [install guide](https://github.com/tenishai/gojira/blob/main/docs/install.md)
steps 1 to 4: the values file, `make install` (terraform makes the cluster), the
UI through a port-forward, registering the cluster. Three values in its
`gojira.env` are for this stack:

```bash
# the roles the room's contract asks for (stack.yaml, kobo: identity.gcp_roles); each
# role an app may ask for must be here, or Gojira refuses the provision
GOJIRA_GRANTABLE_ROLES=roles/aiplatform.user,roles/artifactregistry.reader,roles/container.developer,roles/iam.serviceAccountAdmin,roles/resourcemanager.projectIamAdmin,roles/storage.admin
# the ClusterRole kobo's cluster_objects applies and its contract binds
GOJIRA_GRANTABLE_CLUSTER_ROLES=kobo-room
# which cluster each environment name means; the stack file's apps say `dev`
GOJIRA_ENVIRONMENT_CLUSTERS={dev: gojira-sandbox}
```

Register the cluster under the name you will put in `stack.yaml` (`cluster.name`).
You leave this step with Gojira answering on `http://127.0.0.1:8080` through a
port-forward, one registered cluster, and no apps.

## 2. The GitHub App

Gojira acts on your repos as a GitHub App, and so does the room. One App serves
both. Create it in your organization following Gojira's
[github-app-setup.md](https://github.com/tenishai/gojira/blob/main/docs/github-app-setup.md)
sections 1 to 3, and install it on **all repositories** of the org: the deploy
repos do not exist yet, kobo's config repo does not either, and every agent the
room makes is a new repo.

Keep two things: the App's numeric id (`gojira.env` has it already) and its
private key. Put the key where `stack.yaml` expects it:

```bash
mkdir -p ~/.config/stack && mv ~/Downloads/<app>.*.private-key.pem ~/.config/stack/github-app.pem
chmod 600 ~/.config/stack/github-app.pem
```

The installation id is on the App's installation page (the number at the end of
`github.com/organizations/acme/settings/installations/<id>`); kobo's template
wants it in step 3.

## 3. Your copy of the stack file

```bash
git clone https://github.com/tenishai/stack && cd stack
uv venv .venv && uv sync --locked
cp -r examples/stack.yaml examples/stack ./
```

Edit `stack.yaml`: `acme` → your org, `acme-agents` → your project, the
`kube_context` and `cluster.name` to what step 1 wrote and registered, and the
`image:` lines to the digests on each upstream's release page (a tag moves, a
digest does not). Edit `stack/kobo-config.template.yaml` the same way, and put
the App id and installation id from step 2 in its last lines. Leave
`{gojira_token}` as it is: it is filled when the Secret is written. Nothing in
these files is secret; keep them in a repo of yours if you like.

```bash
.venv/bin/stack check stack.yaml
```

reads both files and names anything missing. It does not touch the cluster.

## 4. Install

```bash
.venv/bin/stack install stack.yaml --merge-setup-prs --cbl <path to cbl>
```

or `stack ui`, which puts the same steps behind buttons on `localhost:8097`
with the run's lines live. What you will see, in order:

1. The cluster is registered (already, from step 1: "exists").
2. **litellm**: the deploy repo is created and filled, Gojira connects it and
   opens a setup PR on it; `--merge-setup-prs` merges it (the press is the
   review: the repo exists for exactly this content). Its CI copies the image
   and applies; pods come up.
3. **cabal**: the same, then the installer catches cabal's one-time bootstrap
   code from its log, logs in with `cbl`, and mints the token kobo will hold.
4. **kobo**: the same, with the ClusterRole applied first and three Secrets.

Rerunning is safe: every step reads before it writes, and a second run says
"exists" down the list. Two pauses are yours:

- **A setup PR your org's rules will not let the installer merge** (a required
  review): the run says so with the link and waits; approve it and the run
  continues on its own.
- **A deploy that fails on a fresh identity**: Google IAM takes about a minute
  to make a new service account usable; the installer retries once by itself.
  A second failure stops with the Actions URL.

## 5. The room's org

kobo keeps an org's catalogs, models and groups in a config repo (`org_repo` in
the template) and its personas and skills in two buckets. Its bootstrap makes
all of them, idempotently, with the App's own credentials, from inside the pod:

```bash
kubectl -n kobo exec deploy/kobo -- python -m kobo_api.bootstrap
```

It creates `acme/kobo-config` with an `org.yaml`, the two buckets, the skills
repo with its publish workflow, and seeds the default personas. Then restart
the room so it reads the org config:

```bash
kubectl -n kobo rollout restart deploy/kobo
```

## 6. Open the room

```bash
kubectl -n kobo port-forward svc/kobo 8090:80
```

`http://localhost:8090`. Create an agent: the room writes its repo in your org,
Gojira deploys it, and it answers in the room's chat. That is the acceptance.
The room has no sign-in proxy in this shape (`iap_audience` is empty), so it is
reachable only through your port-forward; Gojira's install guide shows how to
put a front door with Identity-Aware Proxy on a service when you want one.

## Upgrading

A new release of an app is a new `image:` line. `stack upgrade stack.yaml <app>`
or the UI's release picker (it reads `source_image:`) rewrites the line, copies
the image, and waits until the pods run the new digest. See the README.

## Removing it

`make uninstall` in your Gojira checkout removes the apps, their identities and
namespaces, and Gojira; `--purge` the cluster too. The deploy repos, kobo's
config repo and the buckets stay yours to delete. See Gojira's install guide,
"Removing it".
