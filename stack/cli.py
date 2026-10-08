"""`stack install <stack.yaml>`: the tenish stack onto a running Gojira.

    stack install examples/dogfood.yaml                # waits for a person on each setup PR
    stack install examples/dogfood.yaml --merge-setup-prs   # a first install of repos you own
    stack check examples/dogfood.yaml                  # parse the file, resolve nothing

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

from stack.clients import CblCLI, GhCLI, HttpGojira, KubectlCLI, ToolError
from stack.model import Stack
from stack.steps import Clients, Installer, Options


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stack")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("install", "check"):
        s = sub.add_parser(name)
        s.add_argument("stack_file")
    s = sub.choices["install"]
    s.add_argument("--merge-setup-prs", action="store_true",
                   help="merge every setup PR yourself (a first install of repos you own)")
    s.add_argument("--repos", default="", help="directory holding clones of the apps' repos")
    s.add_argument("--cbl", default=os.environ.get("STACK_CBL", ""), help="path to a cbl binary")
    s.add_argument("--cabal-port", type=int, default=17497)
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
        gojira = HttpGojira(kubectl, stack.gojira.url, stack.gojira.namespace)
    except ToolError as exc:
        print(f"cannot reach Gojira: {exc}", file=sys.stderr)
        return 1
    cabal = None
    cabal_app = next((x for x in stack.apps if x.bootstrap == "cabal"), None)
    pf = None
    if cabal_app is not None:
        if not a.cbl:
            print("this stack bootstraps cabal: pass --cbl <path to cbl> (or STACK_CBL)", file=sys.stderr)
            return 2
        pf = kubectl.port_forward(cabal_app.namespace, f"svc/{cabal_app.name}", a.cabal_port, 80)
        cabal = CblCLI(a.cbl, f"http://127.0.0.1:{a.cabal_port}", str(path.parent / ".cabal-home"))
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
        if pf:
            pf.terminate()
    print(f"\ndone: {len(report.done)} steps" + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
