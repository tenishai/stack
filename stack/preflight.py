"""Preflight: what is missing, in the operator's own words, before a run.

Each check answers ok / warn / fail with a sentence that names the thing
and, where there is one, the fix. The UI runs under the operator's own
credentials and can grant nothing; naming the missing permission is most of
the independence battle (stack#12). The pieces are pure over the hands
(tests run them on fakes); `preflight` composes them for real and turns a
hand that cannot be opened into a fail row instead of a traceback.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from stack.clients import GhCLI, GitHubClient, GojiraClient, KubectlClient, ToolError
from stack.model import Stack
from stack.runs import Target, open_gojira
from stack.steps import DIGEST, ref_parts


@dataclass
class Check:
    what: str
    status: str   # ok | warn | fail
    detail: str = ""


def check_files(stack: Stack, target: Target) -> list[Check]:
    """Every file the stack file names must exist on this machine."""
    out = []
    base = target.stack_file.parent
    for app in stack.apps:
        named: list[tuple[str, Path]] = []
        for obj in (*app.configmaps.values(), *app.secrets.values()):
            for src in obj.values():
                if src.kind in ("file", "template"):
                    named.append((f"{src.kind}:{src.arg}", src.path if src.path.is_absolute() else base / src.path))
        named += [(f, base / f) for f in app.base_files]
        named += [(f, target.repo_root / app.repo.split("/")[1] / f) for f in app.cluster_objects]
        for label, path in named:
            if path.exists():
                out.append(Check(f"{app.name}: {label}", "ok"))
            else:
                out.append(Check(f"{app.name}: {label}", "fail", f"no such file: {path}"))
    return out


def check_tools(stack: Stack, target: Target, which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    out = []
    needed = ["kubectl", "gh"]
    if any(a.deploy_repo for a in stack.apps):
        needed.append("crane")
    for tool in needed:
        path = which(tool)
        out.append(Check(tool, "ok", path or "") if path
                   else Check(tool, "fail", f"{tool} is not on PATH"
                              + (" (brew install crane); upgrades copy images with it" if tool == "crane" else "")))
    if any(a.bootstrap == "cabal" for a in stack.apps):
        if not target.cbl:
            out.append(Check("cbl", "fail", "this stack bootstraps cabal: the profile needs the path to a cbl "
                                            "(cabal's own venv, .venv/bin/cbl)"))
        elif not Path(target.cbl).exists():
            out.append(Check("cbl", "fail", f"no such file: {target.cbl}"))
        else:
            out.append(Check("cbl", "ok", target.cbl))
    return out


def check_github(stack: Stack, github: GitHubClient) -> list[Check]:
    out = []
    try:
        out.append(Check("gh login", "ok", f"logged in as {github.login()}"))
    except ToolError as exc:
        return [Check("gh login", "fail", str(exc))]
    for org in sorted({a.connected_repo.split("/")[0] for a in stack.apps}):
        why = github.org_access(org)
        out.append(Check(f"gh: {org}", "fail", why) if why else Check(f"gh: {org}", "ok", "repos visible"))
    for app in stack.apps:
        repo = app.connected_repo
        if github.repo_exists(repo):
            out.append(Check(f"{app.name}: {repo}", "ok"))
        elif app.deploy_repo:
            out.append(Check(f"{app.name}: {repo}", "warn", "not created yet; Install makes it"))
        else:
            out.append(Check(f"{app.name}: {repo}", "fail", "gh cannot see this repo"))
    return out


def check_cluster(stack: Stack, kubectl: KubectlClient, gojira: GojiraClient) -> list[Check]:
    """Each app on the Gojira: its row, its pods, and what it runs against
    what the file pins. A differing pin is a warn, not a fail: Upgrade is the
    answer, and the row says so."""
    out = []
    rows = {r.name: r for r in gojira.apps()}
    for app in stack.apps:
        row = rows.get(app.name)
        if row is None:
            out.append(Check(f"{app.name}: Gojira", "warn", "not connected; Install does it"))
            continue
        if row.status == "active":
            out.append(Check(f"{app.name}: Gojira", "ok", "active"))
        elif row.status == "awaiting_pr":
            out.append(Check(f"{app.name}: Gojira", "warn", f"waiting on a setup PR: {row.setup_pr_url}"))
        else:
            out.append(Check(f"{app.name}: Gojira", "fail", f"status {row.status}; see the app's page on Gojira"))
        ready, total = kubectl.pods_ready(app.namespace)
        if not total:
            out.append(Check(f"{app.name}: pods", "warn", f"none in namespace {app.namespace}"))
        else:
            out.append(Check(f"{app.name}: pods", "ok" if ready == total else "warn", f"{ready}/{total} ready"))
        running = kubectl.deployed_image(app.namespace, app.name)
        if not running:
            continue
        _, _, pinned = ref_parts(app.image)
        got = (DIGEST.search(running) or [None, ""])[1]
        if pinned and got and pinned != got:
            out.append(Check(f"{app.name}: image", "warn",
                             f"runs {got[:19]}…, the file pins {pinned[:19]}…; Upgrade lands it"))
        elif pinned:
            out.append(Check(f"{app.name}: image", "ok", f"runs the pinned {pinned[:19]}…"))
        else:
            out.append(Check(f"{app.name}: image", "ok", f"runs {_short(running)}"))
    return out


def _short(image: str) -> str:
    """`…/apps/kobo:v1@sha256:<64>` as `kobo:v1@sha256:<12>…`: the registry path
    is the install's own and the digest is a receipt, not reading matter."""
    head, _, digest = image.partition("@")
    return head.split("/")[-1] + (f"@{digest[:19]}…" if digest else "")


def preflight(stack: Stack, target: Target, which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    out = check_tools(stack, target, which) + check_files(stack, target)
    try:
        out += check_github(stack, GhCLI(target.env))
    except ToolError as exc:
        out.append(Check("gh login", "fail", str(exc)))
    try:
        kubectl, gojira = open_gojira(stack, target, say=lambda _: None)
    except ToolError as exc:
        out.append(Check("Gojira", "fail", str(exc)))
        return out
    try:
        out.append(Check("Gojira", "ok", f"context {kubectl.current_context()}, {gojira.url}"))
        out += check_cluster(stack, kubectl, gojira)
    finally:
        gojira.close()
    return out
