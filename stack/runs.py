"""The engine behind both the command line and the buttons.

`perform` is what `stack install` and `stack upgrade` do once the arguments
are parsed: open the hands on the install (kubectl on the pinned context,
Gojira through its port-forward, gh, crane, cbl), run the steps, report
through `say`. The CLI prints each line; the UI keeps them in a `Run` and
shows them while they happen.

A `Target` is what a command line and a profile both reduce to: the stack
file, where the repo clones and `cbl` are, and the environment every tool
call is pinned to (KUBECONFIG, CLOUDSDK_CORE_ACCOUNT). Runs happen one at a
time — one operator, one machine — and live in memory: `stack ui` restarted
forgets them, the cluster and the repos remember.
"""

from __future__ import annotations

import re
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from stack.clients import CblCLI, CraneCLI, CraneClient, GhCLI, HttpGojira, KubectlCLI, ToolError
from stack.model import App, Stack
from stack.steps import Clients, Installer, Options, Report, ref_parts

LEVELS = ("head", "info", "ok", "warn", "fail")
VERSION_TAG = re.compile(r"v?\d+(\.\d+)+")


@dataclass
class Target:
    stack_file: Path
    repos: Path | None = None       # default: the stack file's directory
    cbl: str = ""
    cabal_port: int = 17497
    env: dict[str, str] = field(default_factory=dict)

    @property
    def repo_root(self) -> Path:
        return self.repos or self.stack_file.parent


def open_gojira(stack: Stack, target: Target, say: Callable[[str], None] = print) -> tuple[KubectlCLI, HttpGojira]:
    """kubectl on the install's context and Gojira behind it, or a ToolError
    that says which context was looked at — before anything is touched."""
    kubectl = KubectlCLI(stack.gojira.kube_context, env=target.env)
    context = kubectl.current_context()
    if not kubectl.namespace_exists(stack.gojira.namespace):
        # A stranger's current context can be any cluster at all, and a
        # missing KUBECONFIG is the usual cause.
        raise ToolError(f"kubectl context {context!r} has no namespace {stack.gojira.namespace!r}.\n"
                        "Is KUBECONFIG the install's (the file `make install` wrote)? "
                        "Pin the context with gojira.kube_context in the stack file.")
    say(f"Gojira: context {context}, namespace {stack.gojira.namespace}")
    return kubectl, HttpGojira(kubectl, stack.gojira.url, stack.gojira.namespace)


def perform(target: Target, action: str, apps: list[str], *, merge_setup_prs: bool = False,
            yes: bool = False, merge_pins: bool = False, say: Callable[[str], None] = print,
            on_pause: Callable[[Path, str], threading.Event | None] | None = None,
            on_resume: Callable[[], None] | None = None) -> Report:
    """`stack install` or `stack upgrade` on `target`. Raises ToolError with
    the sentence to show; never prints a traceback. `on_pause(path, why)` is
    told when the run waits for a file a person must put there (criterion 5)
    and may answer an Event that cuts the wait short; `on_resume()` when the
    file is there. The CLI passes neither and the run polls."""
    def _no_pause(path: Path, why: str) -> threading.Event | None:
        return None

    pause_cb: Callable[[Path, str], threading.Event | None] = on_pause or _no_pause
    resume_cb: Callable[[], None] = on_resume or (lambda: None)
    stack = Stack.load(target.stack_file)
    try:
        kubectl, gojira = open_gojira(stack, target, say)
    except ToolError as exc:
        raise ToolError(f"cannot reach Gojira: {exc}") from None
    env = target.env
    cabal = None
    try:
        if action == "upgrade":
            clients = Clients(gojira=gojira, kubectl=kubectl, github=GhCLI(env), crane=CraneCLI(env))
            opts = Options(stack_dir=target.stack_file.parent, repo_root=target.stack_file.parent,
                           merge_setup_prs=merge_setup_prs, yes=yes, merge_pins=merge_pins, say=say, on_pause=pause_cb, on_resume=resume_cb)
            return Installer(stack, clients, opts).upgrade(apps)
        if action != "install":
            raise ToolError(f"no such action {action!r}")
        cabal_app = next((x for x in stack.apps if x.bootstrap == "cabal"), None)
        if cabal_app is not None and apps and cabal_app.name not in apps \
                and not any(x.needs_cabal() for x in stack.apps if x.name in apps):
            cabal_app = None  # the named apps neither are cabal nor mint on it
        if cabal_app is not None:
            if not target.cbl:
                raise ToolError("this stack bootstraps cabal: pass --cbl <path to cbl> (or STACK_CBL)")
            ns, svc, port = cabal_app.namespace, f"svc/{cabal_app.name}", target.cabal_port
            cabal = CblCLI(target.cbl, f"http://127.0.0.1:{port}", str(target.stack_file.parent / ".cabal-home"),
                           forward=lambda: kubectl.port_forward(ns, svc, port, 80), env=env)
        clients = Clients(gojira=gojira, kubectl=kubectl, github=GhCLI(env), cabal=cabal)
        opts = Options(stack_dir=target.stack_file.parent, repo_root=target.repo_root,
                       merge_setup_prs=merge_setup_prs, say=say, on_pause=pause_cb, on_resume=resume_cb)
        return Installer(stack, clients, opts).install(apps)
    finally:
        gojira.close()
        if cabal is not None:
            cabal.close()


# --- releases: what the stack file could pin next ------------------------------


def available_tags(app: App, crane: CraneClient, limit: int = 20) -> list[str]:
    """The version-looking tags published at `source_image:`, newest first."""
    if not app.source_image:
        raise ToolError(f"{app.name}: no source_image: in the stack file to list releases from")
    tags = [t for t in crane.tags(app.source_image) if VERSION_TAG.fullmatch(t)]
    tags.sort(key=lambda t: tuple(int(x) for x in re.findall(r"\d+", t)), reverse=True)
    return tags[:limit]


def data_pvc(app: App, github) -> str:
    """The PVC the app's deploy repo mounts (`data_mount.pvc`), or "" — what
    the backup gate is about, so the page can name it instead of asking
    "if it has one"."""
    if not app.deploy_repo:
        return ""
    try:
        doc = __import__("yaml").safe_load(github.read_file(app.deploy_repo, "gojira.yaml")) or {}
    except (ToolError, Exception):
        return ""
    return str((doc.get("data_mount") or {}).get("pvc") or "")


def set_image(target: Target, app_name: str, tag: str, crane: CraneClient) -> tuple[str, str]:
    """Rewrite the stack file's `image:` line for `app_name` to `<repo>:<tag>@<digest>`,
    the digest read from `source_image:<tag>` — the receipt the file keeps, and what
    `stack upgrade` verifies after the copy. One line changes; the rest of the file,
    comments included, is left as the operator wrote it. Returns (old, new)."""
    stack = Stack.load(target.stack_file)
    app = next((a for a in stack.apps if a.name == app_name), None)
    if app is None:
        raise ToolError(f"not in the stack file: {app_name}")
    if not app.source_image:
        raise ToolError(f"{app.name}: no source_image: in the stack file to upgrade from")
    digest = crane.digest(f"{app.source_image}:{tag}")   # a tag that isn't published stops here
    repo, _, _ = ref_parts(app.image)
    line = re.compile(r"(?m)^([ \t]*)image:[ \t]*" + re.escape(repo) + r"(?::[^\s@#]+)?(?:@sha256:[0-9a-f]{64})?[ \t]*(?:#.*)?$")
    text = target.stack_file.read_text()
    found = line.findall(text)
    if len(found) != 1:
        raise ToolError(f"{app.name}: expected one image: line naming {repo} in {target.stack_file}, found {len(found)}")
    new = f"{repo}:{tag}@{digest}"
    text = line.sub(lambda m: f"{m.group(1)}image: {new}   # {tag}, pinned by stack ui", text, count=1)
    after = Stack.model_validate(__import__("yaml").safe_load(text))   # still a stack file, with the new pin
    if next(a for a in after.apps if a.name == app_name).image != new:
        raise ToolError(f"{app.name}: the rewritten file does not carry the new image")
    target.stack_file.write_text(text)
    return app.image, new


# --- runs, kept for the page ---------------------------------------------------


@dataclass
class Line:
    level: str
    text: str


@dataclass
class Pause:
    """Slice 5 (criterion 5): the run is waiting for a person to put a file where
    the stack file names it. The page shows the path and what the file is for,
    never its contents; Done sets the event and the engine looks again at once."""
    path: str
    why: str
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class Run:
    id: int
    profile: str
    kind: str          # check, install, upgrade
    what: str          # the apps named, for the page
    started: float = field(default_factory=time.time)
    finished: float | None = None
    ok: bool | None = None
    lines: list[Line] = field(default_factory=list)
    paused: Pause | None = None

    @property
    def status(self) -> str:
        if self.finished is None:
            return "paused" if self.paused is not None else "running"
        return "done" if self.ok else "failed"

    def add(self, level: str, text: str) -> None:
        assert level in LEVELS
        self.lines.append(Line(level, text))

    def say(self, text: str) -> None:
        """The engine's `say`: `== app` headings, everything else a step."""
        text = text.rstrip()
        if text.startswith("== "):
            self.add("head", text[3:])
        elif text.strip():
            self.add("info", text.strip())


class Runner:
    """One run at a time, in a thread; every run kept until the process ends."""

    def __init__(self):
        self.runs: dict[int, Run] = {}
        self._busy = threading.Lock()

    def active(self) -> Run | None:
        return next((r for r in self.runs.values() if r.finished is None), None)

    def start(self, profile: str, kind: str, what: str, work: Callable[[Run], None]) -> Run | None:
        """The new run, or None when one is already going."""
        if not self._busy.acquire(blocking=False):
            return None
        run = Run(id=len(self.runs) + 1, profile=profile, kind=kind, what=what)
        self.runs[run.id] = run

        def go():
            try:
                work(run)
                run.ok = True
            except ToolError as exc:
                run.add("fail", f"stopped: {exc}")
                run.ok = False
            except Exception as exc:  # a bug, not a tool's no: still shown, never a hung page
                run.add("fail", f"crashed: {exc!r}")
                run.add("info", traceback.format_exc().strip().splitlines()[-1])
                run.ok = False
            finally:
                run.finished = time.time()
                self._busy.release()

        threading.Thread(target=go, name=f"stack-run-{run.id}", daemon=True).start()
        return run
