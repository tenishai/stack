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
| liminal | the tool gateway: agents reach vendors (GitHub, Datadog, the web) through it, with credentials cabal holds | `ghcr.io/tenishai/liminal` |
| kobo | the room: a web UI where people make and talk to agents | `ghcr.io/tenishai/kobo` |

Each app gets a repo of yours, `<org>/infra-<app>-deploy`, holding only a
`gojira.yaml` and the upstream's `k8s/base`. The installer writes it, Gojira
connects it, and its CI copies the published image into your project's own
registry and applies the manifests. Nothing is built.

## Before you start

| You need | Notes |
|---|---|
| A GCP project with billing, and Owner on it | A throwaway. Gojira's identity gets broad IAM in it, and so does the room's. Fine for a sandbox and nowhere else. |
| A GitHub organization you administer | The deploy repos, kobo's config repo and every agent repo are created there. |
| `gcloud`, `kubectl`, `gh`, `terraform`, `uv`, `crane` | `gcloud components install gke-gcloud-auth-plugin` too. `crane` copies images (`brew install crane`). No Docker. |
| `gcloud` and `gh` logged in | `gcloud auth login` as the project's owner; `gh auth login` as an account that can create repos in the org. |
| cabal's `cbl` | The switchboard's CLI, from a checkout of tenishai/cabal: `uv venv .venv && uv sync` there, then `.venv/bin/cbl`. The installer logs in to cabal with it once and mints the tokens the other apps hold. |
| About an hour | Gojira's install is sixteen minutes of it; the rest is reading. |

> More than one Google account on this machine? `kubectl` caches its token per
> cluster, not per account. Give this install its own kubeconfig
> (`~/.kube-<name>/config`) and name it on the profile below.

## 1. The page

```bash
git clone https://github.com/tenishai/stack && cd stack
uv venv .venv && uv sync --locked --extra ui
git clone https://github.com/tenishai/gojira ../gojira     # Gojira's own install scripts
.venv/bin/stack ui
```

`localhost:8097`. Add a profile: a name; the stack file
`examples/stack.yaml` as it is (nothing in it is edited); the kubeconfig this
install will use; your Google account (`CLOUDSDK_CORE_ACCOUNT`, when the machine
has more than one); an env file path for Gojira's values, say
`~/.config/stack/<name>/gojira.env` (it need not exist); and the Gojira
checkout, `../gojira`.

## 2. Four things

The profile page opens on one card: your GitHub organization, your GCP project,
the region and the cluster name. Press **Save and write the env file**. The
page writes Gojira's values file from them and from the stack file, and shows
what the file now holds:

| Written | From |
|---|---|
| `PROJECT`, `REGION`, `CLUSTER`, `GOJIRA_TERRAFORM=1` | the four; terraform makes the cluster |
| `GOJIRA_GRANTABLE_ROLES` | every role an app's contract asks for, read from the upstreams' `gojira.yaml` on GitHub with the stack file's `contract:` over them |
| `GOJIRA_GRANTABLE_CLUSTER_ROLES` | every `cluster_roles` the same way (kobo's `kobo-room`) |
| `GOJIRA_ENVIRONMENT_CLUSTERS` | each environment the apps name, meaning this cluster |

The other knobs come from Gojira's `gojira.env.example` with their docs, and the
env form (the link in the strip) edits any of them. The same four fill the stack
file's `{org}`, `{project}`, `{region}` and `{cluster}` from now on, and the
kobo template's. From a terminal, `stack env examples/stack.yaml --env-file
<path> --gojira-repo ../gojira --set org=… --set project=… --set region=… --set
cluster=…` writes the same file.

## 3. The GitHub App

Gojira acts on your repos as a GitHub App, and so does the room. One App serves
both. The page shows a card with the link to create it in your organization and
the exact permissions (read from Gojira's
[github-app-setup.md](https://github.com/tenishai/gojira/blob/main/docs/github-app-setup.md)).
Install it on **all repositories** of the org: the deploy repos do not exist
yet, kobo's config repo does not either, and every agent the room makes is a
new repo. Download the private key, put it somewhere only you read
(`chmod 600`), and type the App's id and the key's path on the card. **Done**
checks the key answers for that id and the installation covers the org, and
stores only the id and the path in the env file. The installation id is read
from GitHub when it is needed; nothing to copy.

## 4. Install Gojira

**Install Gojira** runs Gojira's `scripts/install.sh` under the env file as a
run with its lines live: terraform makes the cluster and Gojira's identity
(about ten minutes), Cloud Build builds Gojira, the pod comes up, the version
shows on the page. Pressing again later is how Gojira is upgraded.

## 5. Install the stack

Press **Install** on each app in order: litellm, cabal, liminal, kobo (one press
for the whole file is the next increment). From a terminal:

```bash
.venv/bin/stack install examples/stack.yaml --merge-setup-prs --cbl <path to cbl> \
    --env-file <the env file> --set org=… --set project=… --set region=… --set cluster=…
```

What you will see, in order:

1. The cluster is registered under the name you gave (on a second press: "exists").
2. **litellm**: the deploy repo `<org>/infra-litellm-deploy` is created and filled,
   Gojira connects it and opens a setup PR on it; the run merges it (the press is
   the review: the repo exists for exactly this content). Its CI copies the image
   and applies; pods come up.
3. **cabal**: the same, then the installer catches cabal's one-time bootstrap
   code from its log, logs in with `cbl`, and mints the tokens liminal and kobo
   will hold.
4. **liminal**: the same, holding its token.
5. **kobo**: the same, with the ClusterRole applied first and three Secrets; its
   config comes from the template with the four, the App's id and installation id
   and Gojira's token filled in.

Rerunning is safe: every step reads before it writes, and a second run says
"exists" down the list. Two pauses are yours:

- **A setup PR your org's rules will not let the installer merge** (a required
  review): the run says so with the link and waits; approve it and the run
  continues on its own.
- **A deploy that fails on a fresh identity**: Google IAM takes about a minute
  to make a new service account usable; the installer retries once by itself.
  A second failure stops with the Actions URL.

## 6. The room's org

kobo keeps an org's catalogs, models and groups in a config repo (`org_repo` in
the template) and its personas and skills in two buckets. Its bootstrap makes
all of them, idempotently, with the App's own credentials, from inside the pod:

```bash
kubectl -n kobo exec deploy/kobo -- python -m kobo_api.bootstrap
```

It creates `<org>/kobo-config` with an `org.yaml`, the two buckets, the skills
repo with its publish workflow, and seeds the default personas. Then restart
the room so it reads the org config:

```bash
kubectl -n kobo rollout restart deploy/kobo
```

## 7. Open the room

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
