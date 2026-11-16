"""`stack install <stack.yaml>`: the tenish stack onto a running Gojira.

    stack install examples/dogfood.yaml                # waits for a person on each setup PR
    stack install examples/dogfood.yaml --merge-setup-prs   # a first install of repos you own
    stack check examples/dogfood.yaml                  # parse the file, resolve nothing
    stack upgrade examples/dogfood.yaml [app …]        # converge changed image: pins (stack#12)
    stack ui                                           # the same, behind buttons on localhost

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
    from stack.clients import ToolError
    from stack.model import Stack
    from stack.runs import Target, perform

    p = argparse.ArgumentParser(prog="stack")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("install", "check", "upgrade"):
        s = sub.add_parser(name)
        s.add_argument("stack_file")
    s = sub.choices["install"]
    s.add_argument("apps", nargs="*", help="install only these apps (default: every app in the file)")
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
    s = sub.add_parser("ui", help="profiles, preflight, install and upgrade behind buttons (localhost)")
    s.add_argument("--port", type=int, default=8097)
    s.add_argument("--profiles", default="", help="the profiles file (default: ~/.config/stack/profiles.yaml)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.WARNING,
                        format="%(message)s")

    if a.cmd == "ui":
        from stack.profiles import profiles_path
        try:
            from stack.ui import serve
        except ImportError:
            print("stack ui needs its extra: pip install 'tenish-stack[ui]'", file=sys.stderr)
            return 2
        serve(Path(a.profiles).expanduser() if a.profiles else profiles_path(), a.port)
        return 0

    path = Path(a.stack_file).expanduser()
    try:
        stack = Stack.load(path)
    except Exception as exc:  # pydantic / yaml: one sentence, no traceback
        print(f"{path}: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 2
    if a.cmd == "check":
        print(f"{path}: {len(stack.apps)} apps, in order: " + ", ".join(x.name for x in stack.apps))
        return 0

    target = Target(stack_file=path, repos=Path(a.repos).expanduser() if getattr(a, "repos", "") else None,
                    cbl=getattr(a, "cbl", ""), cabal_port=getattr(a, "cabal_port", 17497))
    try:
        report = perform(target, a.cmd, a.apps, merge_setup_prs=a.merge_setup_prs,
                         yes=getattr(a, "yes", False))
    except ToolError as exc:
        print(f"\n{exc}" if str(exc).startswith("cannot reach Gojira") else f"\nstopped: {exc}", file=sys.stderr)
        return 1
    print(f"\ndone: {len(report.done)} steps"
          + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
