"""What `stack install` does, one app at a time, in the file's order.

Every step is idempotent: a second run finds each thing present and says so.
The installer does what a terminal can; the one thing it leaves to a person
is merging a setup PR on a repo that person owns (unless the app says
`auto_merge_setup_pr: true`), and while it waits it prints the link.

The order inside one app is the order the dogfood needed (2026-10-08):

  1. cluster objects   kubectl apply, once (a ClusterRole Gojira then binds)
  2. connect           POST /api/v1/apps, or find the existing row
  3. provision         wait until the row is active, awaiting_pr or failed
  4. configmaps        in the app's namespace (which provisioning created)
  5. secrets           same, from the sources in the file
  6. the setup PR      merge it (auto) or wait for a person, then /sync
  7. deploy            a reconnect that came back active deploys nothing
                       (tenishai/gojira#75), so dispatch the workflow by hand
  8. wait              for the app's pods to be ready
  9. bootstrap         cabal only: catch the bootstrap code from its log and
                       log in, so later apps can mint tokens on it

Secret values are resolved in step 5, from `Source`s, and go straight into
kubectl; they are never logged. `generate:` values are made once and kept in
a 600 file beside the stack file so a rerun reuses them (a second run must not
rotate a key the running app already holds).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets as _secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from stack.clients import AppRow, CabalClient, GitHubClient, GojiraClient, KubectlClient, ToolError
from stack.model import App, Source, Stack

log = logging.getLogger("stack")

ACTIVE, AWAITING_PR, FAILED = "active", "awaiting_pr", "failed"
WORKFLOW = "gojira-ci.yaml"
BOOTSTRAP_CODE = r"code=([A-Z0-9]{6})"


@dataclass
class Clients:
    gojira: GojiraClient
    kubectl: KubectlClient
    github: GitHubClient
    cabal: CabalClient | None = None  # set once cabal is up and logged in


@dataclass
class Options:
    stack_dir: Path                      # where generate: values are kept
    repo_root: Path                      # where app repos are checked out (for cluster objects)
    merge_setup_prs: bool = False        # also merge PRs of apps without auto_merge
    poll_seconds: float = 10.0
    pr_wait_seconds: float = 3600.0
    rollout_seconds: float = 900.0
    bootstrap_seconds: float = 300.0     # cabal's code is good for five minutes
    iam_settle_seconds: float = 60.0     # a new deploy identity is usable after about this
    token_ttl_days: int = 90
    say: object = print


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    waited_on: list[str] = field(default_factory=list)

    def note(self, line: str, say) -> None:
        self.done.append(line)
        say(f"  {line}")


class Installer:
    def __init__(self, stack: Stack, clients: Clients, opts: Options):
        self.stack, self.c, self.o = stack, clients, opts
        self.report = Report()

    # --- the whole run ------------------------------------------------------

    def install(self) -> Report:
        say = self.o.say
        say("== cluster")
        self.register_cluster()
        for app in self.stack.apps:
            say(f"== {app.name}")
            self.install_app(app)
        return self.report

    # --- cluster ------------------------------------------------------------

    def register_cluster(self) -> None:
        cl = self.stack.cluster
        if any(c["name"] == cl.name for c in self.c.gojira.clusters()):
            self.report.note(f"cluster {cl.name} registered", self.o.say)
            return
        self.c.gojira.register_cluster(cl.name, cl.gcp_project, cl.location, cl.gke_cluster, cl.ar_repo)
        self.report.note(f"cluster {cl.name} registered (new)", self.o.say)

    # --- one app ------------------------------------------------------------

    def install_app(self, app: App) -> AppRow:
        if app.deploy_repo:
            self.ensure_deploy_repo(app)
        self.apply_cluster_objects(app)
        row = self.connect(app)
        row = self.wait_provisioned(row)
        if row.status == FAILED:
            # The first minute after an install is eventually consistent on
            # Google's side (a WIF provider resurrected by terraform, an IAM
            # policy another provision just wrote: tenishai/gojira#117, #107).
            # One more try after the settle time, then it is a real failure.
            self.o.say(f"  {app.name}: provision failed; trying once more in "
                       f"{self.o.iam_settle_seconds:.0f}s")
            time.sleep(self.o.iam_settle_seconds)
            self.c.gojira.provision(row.id)
            row = self.wait_provisioned(self.c.gojira.app(row.id))
            if row.status == FAILED:
                raise ToolError(f"{app.name}: provision failed twice; see the app's page on Gojira")
            self.report.note(f"{app.name}: provisioned on the second try", self.o.say)
        self.apply_configmaps(app)
        self.apply_secrets(app)
        row = self.settle_setup_pr(app, row)
        self.ensure_deployed(app, row)
        self.wait_ready(app)
        if app.bootstrap == "cabal":
            if self.tokens_still_needed(app):
                self.bootstrap_cabal(app)
            else:
                self.report.note(f"{app.name}: no tokens left to mint, bootstrap skipped", self.o.say)
        return row

    def tokens_still_needed(self, cabal_app: App) -> bool:
        """A rerun on a live stack has every token Secret already; the
        bootstrap code is printed only at startup, so don't go looking."""
        after = self.stack.apps[self.stack.apps.index(cabal_app) + 1:]
        for app in after:
            for name, keys in app.secrets.items():
                if any(s.kind == "cabal-token" for s in keys.values()) \
                        and not self.c.kubectl.secret_exists(app.namespace, name):
                    return True
        return False

    def apply_cluster_objects(self, app: App) -> None:
        for rel in app.cluster_objects:
            path = self.o.repo_root / app.repo.split("/")[1] / rel
            if not path.is_file():
                raise ToolError(f"{app.name}: cluster object {rel} not found at {path} "
                                f"(clone {app.repo} under {self.o.repo_root})")
            out = self.c.kubectl.apply_file(str(path))
            self.report.note(f"{app.name}: applied {rel} ({out.split()[-1] if out else 'ok'})", self.o.say)

    def ensure_deploy_repo(self, app: App) -> None:
        """The repo Gojira connects when the app is a published image: created
        if missing, holding exactly a gojira.yaml that names the image and the
        k8s/base the upstream keeps. Idempotent: identical content pushes
        nothing. The upstream's build_args, arch and node_selector are left
        behind (there is no build, and the platform's arch is the install's);
        `contract:` from the stack file is merged last."""
        files = self.deploy_repo_files(app)
        if not self.c.github.repo_exists(app.deploy_repo):
            self.c.github.create_repo(app.deploy_repo)
            self.report.note(f"{app.name}: created deploy repo {app.deploy_repo}", self.o.say)
        changed = self.c.github.push_files(app.deploy_repo, files, f"deploy {app.image}")
        self.report.note(f"{app.name}: deploy repo {app.deploy_repo} "
                         + (f"updated ({changed} files)" if changed else "current"), self.o.say)

    def deploy_repo_files(self, app: App) -> dict[str, str]:
        upstream = self.o.repo_root / app.repo.split("/")[1]
        base = upstream / "k8s" / "base"
        if not base.is_dir():
            raise ToolError(f"{app.name}: {app.repo} has no k8s/base at {base} (clone it under {self.o.repo_root})")
        doc = {}
        contract = upstream / "gojira.yaml"
        if contract.is_file():
            doc = yaml.safe_load(contract.read_text()) or {}
        for key in ("app", "class", "image", "environments", "cluster", "namespace",
                    "build_args", "arch", "node_selector"):
            doc.pop(key, None)
        doc = {"app": app.name, "class": app.workload_class, "image": app.image,
               "environments": [{"name": app.environment, "cluster": self.stack.cluster.name,
                                 "namespace": app.namespace}], **doc}
        for key, value in app.contract.items():
            doc[key] = {**doc[key], **value} if isinstance(value, dict) and isinstance(doc.get(key), dict) else value
        files = {"gojira.yaml": "# Deploy contract written by the stack installer: the image is published upstream;\n"
                                "# this repo holds what the deploy needs. Change the stack file, not this file.\n"
                                + yaml.safe_dump(doc, sort_keys=False)}
        for path in sorted(p for p in base.iterdir() if p.is_file()):
            files[f"k8s/base/{path.name}"] = path.read_text()
        return files

    def connect(self, app: App) -> AppRow:
        for row in self.c.gojira.apps():
            if row.name == app.name:
                self.report.note(f"{app.name}: connected (app {row.id}, {row.status})", self.o.say)
                if row.status == FAILED:
                    self.c.gojira.provision(row.id)
                    self.report.note(f"{app.name}: provision pressed again", self.o.say)
                return row
        auto = app.auto_merge_setup_pr or self.o.merge_setup_prs
        row = self.c.gojira.connect(app.name, app.connected_repo, app.workload_class, app.environment,
                                    app.namespace, auto)
        self.report.note(f"{app.name}: connected (app {row.id}, new)", self.o.say)
        return row

    def wait_provisioned(self, row: AppRow) -> AppRow:
        deadline = time.monotonic() + self.o.rollout_seconds
        while row.status not in (ACTIVE, AWAITING_PR, FAILED):
            if time.monotonic() > deadline:
                raise ToolError(f"{row.name}: still {row.status} after {self.o.rollout_seconds:.0f}s")
            time.sleep(self.o.poll_seconds)
            row = self.c.gojira.app(row.id)
        return row

    def apply_configmaps(self, app: App) -> None:
        for name, keys in app.configmaps.items():
            if self.c.kubectl.configmap_exists(app.namespace, name):
                self.report.note(f"{app.name}: configmap {name} exists", self.o.say)
                continue
            self.c.kubectl.apply_configmap(app.namespace, name,
                                           {k: self.resolve(app, s) for k, s in keys.items()})
            self.report.note(f"{app.name}: configmap {name} created", self.o.say)

    def apply_secrets(self, app: App) -> None:
        for name, keys in app.secrets.items():
            if self.c.kubectl.secret_exists(app.namespace, name):
                self.report.note(f"{app.name}: secret {name} exists", self.o.say)
                continue
            data = {k: self.resolve(app, s) for k, s in keys.items()}
            self.c.kubectl.apply_secret(app.namespace, name, data)
            del data
            self.report.note(f"{app.name}: secret {name} created ({', '.join(keys)})", self.o.say)

    def settle_setup_pr(self, app: App, row: AppRow) -> AppRow:
        if row.status != AWAITING_PR:
            return row
        url = row.setup_pr_url
        state = self.c.github.pr_state(url) if url else "unknown"
        if state == "open" and (app.auto_merge_setup_pr or self.o.merge_setup_prs):
            self.c.github.merge_pr(url)
            self.report.note(f"{app.name}: merged {url}", self.o.say)
            state = "merged"
        if state == "open":
            self.o.say(f"  {app.name}: waiting for someone to merge {url}")
            self.report.waited_on.append(url)
            deadline = time.monotonic() + self.o.pr_wait_seconds
            while state == "open":
                if time.monotonic() > deadline:
                    raise ToolError(f"{app.name}: {url} still open after {self.o.pr_wait_seconds:.0f}s")
                time.sleep(self.o.poll_seconds)
                state = self.c.github.pr_state(url)
        if state == "merged":
            row = self.c.gojira.sync(row.id)
            self.report.note(f"{app.name}: setup PR merged, synced ({row.status})", self.o.say)
            return row
        raise ToolError(f"{app.name}: setup PR {url} is {state}; reopen it or press Provision again")

    def ensure_deployed(self, app: App, row: AppRow) -> None:
        """A reconnect that matched the repo comes back active with nothing
        running (tenishai/gojira#75): dispatch the workflow ourselves, and
        watch it. A deploy identity made seconds ago is not yet usable
        (Google IAM propagates in about a minute: the first stranger run
        failed on "unable to acquire impersonated credentials"), so a failed
        first run is dispatched once more after the settle time."""
        _, total = self.c.kubectl.pods_ready(app.namespace)
        if total:
            return
        status, _ = self.c.github.last_run(app.connected_repo, WORKFLOW)
        if status not in ("queued", "in_progress"):
            self.c.github.dispatch(app.connected_repo, WORKFLOW)
            self.report.note(f"{app.name}: deploy dispatched (namespace was empty)", self.o.say)
        else:
            self.report.note(f"{app.name}: deploy already running", self.o.say)
        if self.wait_run(app) == "success":
            return
        self.o.say(f"  {app.name}: the deploy failed; a fresh identity takes about a minute "
                   f"to be usable, trying once more in {self.o.iam_settle_seconds:.0f}s")
        time.sleep(self.o.iam_settle_seconds)
        self.c.github.dispatch(app.connected_repo, WORKFLOW)
        self.report.note(f"{app.name}: deploy dispatched again", self.o.say)
        if self.wait_run(app) != "success":
            raise ToolError(f"{app.name}: the deploy failed twice; read the latest {WORKFLOW} run on "
                            f"https://github.com/{app.connected_repo}/actions")

    def wait_run(self, app: App) -> str:
        """The conclusion of the latest workflow run once it completes."""
        deadline = time.monotonic() + self.o.rollout_seconds
        time.sleep(min(self.o.poll_seconds, 5))  # the dispatch takes a moment to appear
        while True:
            status, conclusion = self.c.github.last_run(app.connected_repo, WORKFLOW)
            if status == "completed":
                return conclusion
            if time.monotonic() > deadline:
                raise ToolError(f"{app.name}: the deploy is still {status} after "
                                f"{self.o.rollout_seconds:.0f}s")
            time.sleep(self.o.poll_seconds)

    def wait_ready(self, app: App) -> None:
        deadline = time.monotonic() + self.o.rollout_seconds
        while True:
            ready, total = self.c.kubectl.pods_ready(app.namespace)
            if total and ready == total:
                self.report.note(f"{app.name}: {ready}/{total} pods ready", self.o.say)
                return
            if time.monotonic() > deadline:
                raise ToolError(f"{app.name}: {ready}/{total} pods ready after {self.o.rollout_seconds:.0f}s")
            time.sleep(self.o.poll_seconds)

    def bootstrap_cabal(self, app: App) -> None:
        """cabal prints a one-time operator code at startup, good for five
        minutes; catch it and log in so later apps can mint tokens.

        A pod that has been up longer than that (a rerun, or a build that
        took its time) holds an expired code, so the login is tried with
        what the log shows from the last few minutes, and on a miss or a
        refusal the deployment is restarted once for a fresh one."""
        if self.c.cabal is None:
            raise ToolError("no cabal client configured (cbl)")
        code = self.c.kubectl.logs_grep(app.namespace, app.name, BOOTSTRAP_CODE, since="4m")
        if code:
            try:
                self.c.cabal.login(code)
                self.report.note(f"{app.name}: operator logged in with the bootstrap code", self.o.say)
                return
            except ToolError as exc:
                self.o.say(f"  {app.name}: the code in the log was refused ({exc}); restarting for a fresh one")
        else:
            self.o.say(f"  {app.name}: no fresh bootstrap code in the log; restarting for one")
        self.c.kubectl.restart(app.namespace, app.name)
        deadline = time.monotonic() + self.o.bootstrap_seconds
        while True:
            code = self.c.kubectl.logs_grep(app.namespace, app.name, BOOTSTRAP_CODE, since="4m")
            if code:
                break
            if time.monotonic() > deadline:
                raise ToolError(f"{app.name}: no bootstrap code in its log after a restart; "
                                "is `cabal.bootstrap_code` set to `log`?")
            time.sleep(self.o.poll_seconds)
        self.c.cabal.login(code)
        self.report.note(f"{app.name}: restarted, operator logged in with the fresh code", self.o.say)

    # --- values -------------------------------------------------------------

    def resolve(self, app: App, s: Source) -> str:
        if s.kind == "literal":
            return s.arg
        if s.kind == "file":
            return self._path(s).read_text().rstrip("\n")
        if s.kind == "gojira-token":
            return self.c.gojira.api_token()
        if s.kind == "template":
            return self._path(s).read_text().replace("{gojira_token}", self.c.gojira.api_token()) \
                .replace("<GOJIRA_API_TOKEN>", self.c.gojira.api_token())
        if s.kind == "generate":
            return self._generated(app, s)
        if s.kind == "cabal-token":
            if self.c.cabal is None:
                raise ToolError(f"{app.name}: needs a cabal token but cabal is not up yet")
            return self.c.cabal.mint(s.cabal_sub, s.cabal_scope, self.o.token_ttl_days)
        raise ToolError(f"unknown source {s.kind}")

    def _path(self, s: Source) -> Path:
        """A relative `file:`/`template:` path is relative to the stack file."""
        return s.path if s.path.is_absolute() else self.o.stack_dir / s.path

    def _generated(self, app: App, s: Source) -> str:
        """Made once, kept in a 600 file next to the stack file, reused after.
        A named value (`generate:32:litellm-master-key`) is one file for the
        whole stack, so every app that names it gets the same bytes."""
        tag = s.gen_name or f"{app.name}-{s.gen_bytes}"
        keep = self.o.stack_dir / ".generated" / tag
        keep.parent.mkdir(mode=0o700, exist_ok=True)
        if keep.exists():
            return keep.read_text()
        value = base64.b64encode(_secrets.token_bytes(s.gen_bytes)).decode()
        fd = os.open(keep, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value)
        return value
