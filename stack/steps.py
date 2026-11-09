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

`stack upgrade` (stack#12) converges a changed stack file onto a running
install, one image app at a time: copy the release image into the install
registry if its digest isn't there (crane, from `source_image:`), land the
one-line digest pin on the deploy repo (a PR someone merges, or a direct
push, per the file's `upgrade_gate`), press provision, let the setup PR
settle, and wait until the deployment runs the new digest. An app that
carries a `data_mount` is refused without --yes: back up the PVC first.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets as _secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from stack.clients import (
    AppRow,
    CabalClient,
    CraneClient,
    GitHubClient,
    GojiraClient,
    KubectlClient,
    ToolError,
)
from stack.model import App, Source, Stack

log = logging.getLogger("stack")

ACTIVE, AWAITING_PR, FAILED = "active", "awaiting_pr", "failed"
WORKFLOW = "gojira-ci.yaml"
BOOTSTRAP_CODE = r"code=([A-Z0-9]{6})"
IMAGE_LINE = re.compile(r"(?m)^image:.*$")
DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})")


def ref_parts(image: str) -> tuple[str, str, str]:
    """`repo[:tag][@sha256:…]` split into (repo, tag, digest), empties allowed."""
    head, _, digest = image.partition("@")
    if ":" in head.split("/")[-1]:
        repo, _, tag = head.rpartition(":")
    else:
        repo, tag = head, ""
    return repo, tag, digest


@dataclass
class Clients:
    gojira: GojiraClient
    kubectl: KubectlClient
    github: GitHubClient
    cabal: CabalClient | None = None  # set once cabal is up and logged in
    crane: CraneClient | None = None  # upgrades only; install never copies images


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
    yes: bool = False                    # upgrade: the data_mount backup gate was answered
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

    def install(self, names: list[str] | None = None) -> Report:
        """Every app in the file's order, or only the named ones (still in the
        file's order): adding one app to a live install must not rewrite the
        others' deploy repos from whatever their upstream clones hold today."""
        say = self.o.say
        names = names or []
        unknown = sorted(set(names) - {a.name for a in self.stack.apps})
        if unknown:
            raise ToolError(f"not in the stack file: {', '.join(unknown)}")
        say("== cluster")
        self.register_cluster()
        for app in self.stack.apps:
            if names and app.name not in names:
                continue
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
        changed = self.ensure_deploy_repo(app) if app.deploy_repo else 0
        self.apply_cluster_objects(app)
        row = self.connect(app, contract_changed=bool(changed))
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

    def ensure_deploy_repo(self, app: App) -> int:
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
        return changed

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
        # An adopted account (identity.gsa) lives in the upstream's own project;
        # this install cannot adopt it (litellm on 2026-10-22: PERMISSION_DENIED
        # listing accounts in another project). Keep the roles, drop the account;
        # `contract:` may name one of this install's own.
        if isinstance(doc.get("identity"), dict):
            doc["identity"].pop("gsa", None)
            if not doc["identity"]:
                doc.pop("identity")
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
        if app.base_files:
            self._add_base_files(app, files)
        return files

    def _add_base_files(self, app: App, files: dict[str, str]) -> None:
        """The install's own files beside the upstream's, each listed in the
        base kustomization: Gojira keeps only the resources a kustomization
        already names (preserve_resources), so a file that isn't listed would
        sit in the repo and leave the build."""
        names = []
        for rel in app.base_files:
            src = Path(rel).expanduser()
            src = src if src.is_absolute() else self.o.stack_dir / src
            if not src.is_file():
                raise ToolError(f"{app.name}: base file {rel} not found at {src}")
            key = f"k8s/base/{src.name}"
            if key in files:
                raise ToolError(f"{app.name}: base file {src.name} would replace {app.repo}'s own; rename it")
            files[key] = src.read_text()
            names.append(src.name)
        kust_key = "k8s/base/kustomization.yaml"
        if kust_key in files:
            doc = yaml.safe_load(files[kust_key]) or {}
        else:
            doc = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                   "resources": sorted(k.split("/")[-1] for k in files
                                       if k.startswith("k8s/base/") and k.endswith((".yaml", ".yml"))
                                       and k.split("/")[-1] not in names)}
        resources = list(doc.get("resources") or [])
        doc["resources"] = resources + [n for n in names if n not in resources]
        files[kust_key] = yaml.safe_dump(doc, sort_keys=False)

    def connect(self, app: App, contract_changed: bool = False) -> AppRow:
        """An app already connected is provisioned again when it failed, or when
        this run changed its deploy repo: the push's own CI fails the contract
        check by design, and only a provision regenerates what deploys."""
        for row in self.c.gojira.apps():
            if row.name == app.name:
                self.report.note(f"{app.name}: connected (app {row.id}, {row.status})", self.o.say)
                if row.status == FAILED or contract_changed:
                    self.c.gojira.provision(row.id)
                    why = "deploy repo changed" if contract_changed else "it had failed"
                    self.report.note(f"{app.name}: provision pressed again ({why})", self.o.say)
                    return self.c.gojira.app(row.id)
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

    # --- upgrade (stack#12) ---------------------------------------------------

    def upgrade(self, names: list[str]) -> Report:
        say = self.o.say
        unknown = sorted(set(names) - {a.name for a in self.stack.apps})
        if unknown:
            raise ToolError(f"not in the stack file: {', '.join(unknown)}")
        for app in self.stack.apps:
            if names and app.name not in names:
                continue
            say(f"== {app.name}")
            self.upgrade_app(app)
        return self.report

    def upgrade_app(self, app: App) -> None:
        if not app.deploy_repo:
            self.report.note(f"{app.name}: built from source (no deploy_repo), upgrade skipped", self.o.say)
            return
        desired = self.ensure_mirrored(app)
        text = self.c.github.read_file(app.deploy_repo, "gojira.yaml")
        m = IMAGE_LINE.search(text)
        if not m:
            raise ToolError(f"{app.name}: no image: line in {app.deploy_repo}'s gojira.yaml")
        current = (DIGEST.search(m.group(0)) or [None, ""])[1]
        if not current:
            # The install wrote the deploy repo's line by tag. What that tag
            # resolves to is the comparison; pinning the same bytes by digest
            # is not an upgrade (an Upgrade pressed with nothing to upgrade,
            # 2026-11-09, opened a pin PR for the image already running).
            ref = m.group(0).split(":", 1)[1].split("#", 1)[0].strip()
            try:
                current = self.c.crane.digest(ref)
            except ToolError:
                current = ""
        if current == desired:
            self.report.note(f"{app.name}: already at {desired[:19]}…, nothing to do", self.o.say)
            return
        doc = yaml.safe_load(text) or {}
        if doc.get("data_mount") and not self.o.yes:
            pvc = (doc["data_mount"] or {}).get("pvc", "?")
            raise ToolError(f"{app.name}: carries data_mount (pvc {pvc}) — back it up first, "
                            "then rerun with --yes")
        self.land_pin(app, text, current, desired)
        # The pin's own push CI completes first — it fails the contract check
        # by design (finding #20's guard: the workflow still carries the old
        # contract sha). Let it finish so the next run wait_rolled watches is
        # the provision's own deploy, whatever its outcome here.
        self.wait_run(app)
        row = next((r for r in self.c.gojira.apps() if r.name == app.name), None)
        if row is None:
            raise ToolError(f"{app.name}: not connected on this Gojira; run `stack install` first")
        self.c.gojira.provision(row.id)
        row = self.wait_provisioned(self.c.gojira.app(row.id))
        row = self.settle_setup_pr(app, row)
        if row.status == FAILED:
            raise ToolError(f"{app.name}: provision failed; see the app's page on Gojira")
        self.wait_rolled(app, desired)
        self.report.note(f"{app.name}: upgraded {current[:19] + '…' if current else '(unpinned)'}"
                         f" -> {desired[:19]}…", self.o.say)

    def ensure_mirrored(self, app: App) -> str:
        """The digest of the stack file's `image:`, copied in from
        `source_image:` when the install registry doesn't hold it yet."""
        if self.c.crane is None:
            raise ToolError("upgrade resolves and copies images with crane, and none is configured")
        repo, tag, pinned = ref_parts(app.image)
        if not tag and not pinned:
            raise ToolError(f"{app.name}: image: needs a tag (or @digest) to upgrade to")
        ref = f"{repo}:{tag}" if tag else f"{repo}@{pinned}"
        try:
            digest = self.c.crane.digest(ref)
        except ToolError:
            if not app.source_image:
                raise ToolError(f"{app.name}: {ref} is not in the install registry and the app names "
                                "no source_image: to copy it from") from None
            if not tag:
                raise ToolError(f"{app.name}: copying from source_image needs a tag on image:") from None
            self.c.crane.copy(f"{app.source_image}:{tag}", ref)
            self.report.note(f"{app.name}: copied {app.source_image}:{tag} into the install registry",
                             self.o.say)
            digest = self.c.crane.digest(ref)
        if pinned and digest != pinned:
            raise ToolError(f"{app.name}: {ref} resolves to {digest[:19]}… but the stack file pins "
                            f"{pinned[:19]}… — the tag moved, or the pin is stale")
        return digest

    def land_pin(self, app: App, text: str, current: str, desired: str) -> None:
        """The one-line digest bump on the deploy repo, by the file's gate."""
        repo, tag, _ = ref_parts(app.image)
        line = f"image: {repo}@{desired}   # {tag or desired[:19]}, pinned by stack upgrade"
        new_text = IMAGE_LINE.sub(line.replace("\\", "\\\\"), text, count=1)
        title = f"upgrade {app.name} to {tag or desired[:19]}"
        if self.stack.upgrade_gate == "push":
            self.c.github.push_files(app.deploy_repo, {"gojira.yaml": new_text}, title)
            self.report.note(f"{app.name}: pin pushed to {app.deploy_repo}", self.o.say)
            return
        body = (f"Opened by `stack upgrade`: {current or '(unpinned)'} -> {desired}.\n\n"
                "The push CI on merge fails the contract check by design; the provision "
                "that follows regenerates the workflow.")
        url = self.c.github.open_pr(app.deploy_repo, {"gojira.yaml": new_text}, title, body,
                                    branch=f"upgrade/{app.name}-{desired[7:19]}")
        self.o.say(f"  {app.name}: waiting for someone to merge {url}")
        self.report.waited_on.append(url)
        deadline = time.monotonic() + self.o.pr_wait_seconds
        state = self.c.github.pr_state(url)
        while state == "open":
            if time.monotonic() > deadline:
                raise ToolError(f"{app.name}: {url} still open after {self.o.pr_wait_seconds:.0f}s")
            time.sleep(self.o.poll_seconds)
            state = self.c.github.pr_state(url)
        if state != "merged":
            raise ToolError(f"{app.name}: the pin PR {url} is {state}")
        self.report.note(f"{app.name}: pin merged ({url})", self.o.say)

    def wait_rolled(self, app: App, digest: str) -> None:
        """Done when the deployment runs the new digest and its pods are ready
        — the receipt that matters, not a green run. A deploy run that started
        after the pin and completed without success fails fast; the pin push's
        own contract-check failure (expected, finding #20's guard) never
        started after the provision, so it doesn't trip this."""
        deadline = time.monotonic() + self.o.rollout_seconds
        saw_running = False
        while True:
            image = self.c.kubectl.deployed_image(app.namespace, app.name)
            ready, total = self.c.kubectl.pods_ready(app.namespace)
            if digest in image and total and ready == total:
                self.report.note(f"{app.name}: rolled, {ready}/{total} pods on {digest[:19]}…", self.o.say)
                return
            status, conclusion = self.c.github.last_run(app.connected_repo, WORKFLOW)
            if status in ("queued", "in_progress"):
                saw_running = True
            elif saw_running and status == "completed" and conclusion != "success":
                raise ToolError(f"{app.name}: the deploy run failed; read "
                                f"https://github.com/{app.connected_repo}/actions")
            if time.monotonic() > deadline:
                raise ToolError(f"{app.name}: pods not on {digest[:19]}… after "
                                f"{self.o.rollout_seconds:.0f}s; see "
                                f"https://github.com/{app.connected_repo}/actions")
            time.sleep(self.o.poll_seconds)

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
