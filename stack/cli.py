"""`stack install <stack.yaml>`: the tenish stack onto a running Gojira.

    stack install examples/dogfood.yaml                # waits for a person on each setup PR
    stack install examples/dogfood.yaml --merge-setup-prs   # a first install of repos you own
    stack check examples/dogfood.yaml                  # parse the file, resolve nothing
    stack upgrade examples/dogfood.yaml [app …]        # converge changed image: pins (stack#12)

Needs on PATH: kubectl (on the install's context), gh (logged in), and for a
stack with `bootstrap: cabal`, a `cbl` (--cbl, or $STACK_CBL). Repos named
in `cluster_objects` are expected under --repos (default: the stack file's
directory), cloned by you.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _shed_pythonpath() -> None:
    """A PYTHONPATH in the environment puts someone else's site-packages
    ahead of this venv's (a shared machine that exports one globally shadowed
    pydantic on the first stranger run, 2026-10-08). Re-exec without it, once."""
    if os.environ.get("PYTHONPATH") and not os.environ.get("STACK_REEXEC"):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["STACK_REEXEC"] = "1"
        os.execve(sys.executable, [sys.executable, "-m", "stack.cli", *sys.argv[1:]], env)


def main(argv: list[str] | None = None) -> int:
    _shed_pythonpath()
    from stack.clients import CblCLI, CraneCLI, GhCLI, HttpGojira, KubectlCLI, ToolError
    from stack.model import Stack
    from stack.steps import Clients, Installer, Options

    p = argparse.ArgumentParser(prog="stack")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("install", "check", "upgrade"):
        s = sub.add_parser(name)
        s.add_argument("stack_file")
    s = sub.choices["install"]
    s.add_argument("--merge-setup-prs", action="store_true",
                   help="merge every setup PR yourself (a first install of repos you own)")
    s.add_argument("--repos", default="", help="directory holding clones of the apps' repos")
    s.add_argument("--cbl", default=os.environ.get("STACK_CBL", ""), help="path to a cbl binary")
    s.add_argument("--cabal-port", type=int, default=17497)
    s.add_argument("-v", "--verbose", action="store_true")
    s = sub.choices["upgrade"]
    s.add_argument("apps", nargs="*", help="apps to upgrade (default: every image app in the file)")
    s.add_argument("--yes", action="store_true",
                   help="the data_mount apps' PVCs are backed up; roll them")
    s.add_argument("--merge-setup-prs", action="store_true",
                   help="merge the regenerated setup PRs yourself")
    s.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.WARNING,
                        format="%(message)s")

    path = Path(a.stack_file).expanduser()
    try:
        stack = Stack.load(path)
    except Exception as exc:  # pydantic / yaml: one sentence, no traceback
        print(f"{path}: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 2
    if a.cmd == "check":
        print(f"{path}: {len(stack.apps)} apps, in order: " + ", ".join(x.name for x in stack.apps))
        return 0

    try:
        kubectl = KubectlCLI(stack.gojira.kube_context)
        context = kubectl.current_context()
        if not kubectl.namespace_exists(stack.gojira.namespace):
            # Said before anything is touched: a stranger's current context can
            # be any cluster at all, and a missing KUBECONFIG is the usual cause.
            print(f"cannot reach Gojira: kubectl context {context!r} has no namespace "
                  f"{stack.gojira.namespace!r}.\nIs KUBECONFIG the install's (the file `make install` wrote)? "
                  "Pin the context with gojira.kube_context in the stack file.", file=sys.stderr)
            return 1
        print(f"Gojira: context {context}, namespace {stack.gojira.namespace}")
        gojira = HttpGojira(kubectl, stack.gojira.url, stack.gojira.namespace)
    except ToolError as exc:
        print(f"cannot reach Gojira: {exc}", file=sys.stderr)
        return 1
    if a.cmd == "upgrade":
        try:
            clients = Clients(gojira=gojira, kubectl=kubectl, github=GhCLI(), crane=CraneCLI())
            opts = Options(stack_dir=path.parent, repo_root=path.parent,
                           merge_setup_prs=a.merge_setup_prs, yes=a.yes)
            report = Installer(stack, clients, opts).upgrade(a.apps)
        except ToolError as exc:
            print(f"\nstopped: {exc}", file=sys.stderr)
            return 1
        finally:
            gojira.close()
        print(f"\ndone: {len(report.done)} steps"
              + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))
        return 0

    cabal = None
    cabal_app = next((x for x in stack.apps if x.bootstrap == "cabal"), None)
    if cabal_app is not None:
        if not a.cbl:
            print("this stack bootstraps cabal: pass --cbl <path to cbl> (or STACK_CBL)", file=sys.stderr)
            return 2
        ns, svc, port = cabal_app.namespace, f"svc/{cabal_app.name}", a.cabal_port
        cabal = CblCLI(a.cbl, f"http://127.0.0.1:{port}", str(path.parent / ".cabal-home"),
                       forward=lambda: kubectl.port_forward(ns, svc, port, 80))
    clients = Clients(gojira=gojira, kubectl=kubectl, github=GhCLI(), cabal=cabal)
    opts = Options(stack_dir=path.parent, repo_root=Path(a.repos).expanduser() if a.repos else path.parent,
                   merge_setup_prs=a.merge_setup_prs)
    try:
        report = Installer(stack, clients, opts).install()
    except ToolError as exc:
        print(f"\nstopped: {exc}", file=sys.stderr)
        return 1
    finally:
        gojira.close()
        if cabal is not None:
            cabal.close()
    print(f"\ndone: {len(report.done)} steps" + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
