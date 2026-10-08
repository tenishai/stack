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
| `file:<path>` | the bytes of a file on your machine (trailing newline stripped) |
| `generate:<n>` | n random bytes, base64, made once and kept in `.generated/` (mode 600) beside the file |
| `gojira-token` | the install's own API token, read from Gojira's Secret |
| `template:<path>` | a file with `{gojira_token}` filled in |
| `cabal-token:<sub>:<scope>` | a 90-day service JWT minted on the cabal this run brought up |
| `literal:<text>` | the text itself; for names, never for secrets |

An app with `cluster_objects` names files in its repo (cloned under `--repos`)
that an operator applies once: a ClusterRole Gojira then binds through
`cluster_roles`. `bootstrap: cabal` marks the switchboard. An app with
`auto_merge_setup_pr: true`, or a run with `--merge-setup-prs`, merges its own
setup PR; otherwise the run prints the link and waits.

## What it needs

`kubectl` on the install's context, `gh` logged in with access to the repos,
and for a stack with cabal, a `cbl` binary (`--cbl`, from cabal's own venv).
Gojira is reached through a port-forward of its Service unless `gojira.url`
is set.

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
