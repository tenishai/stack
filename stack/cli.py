"""`stack install <stack.yaml>`: the tenish stack onto a running Gojira.

    stack install examples/dogfood.yaml                # waits for a person on each setup PR
    stack install examples/dogfood.yaml --merge-setup-prs   # a first install of repos you own
    stack check examples/dogfood.yaml                  # parse the file, resolve nothing
    stack upgrade examples/dogfood.yaml [app …]        # converge changed image: pins (stack#12)
    stack env examples/stack.yaml --env-file gojira.env --set org=… --set project=… \
        --set region=… --set cluster=…                 # write Gojira's values file from the four
    stack ui                                           # the same, behind buttons on localhost

A stack file may carry the tokens {org} {project} {region} {cluster}
(examples/stack.yaml does): --set fills them, or the profile does in the UI.

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
    for name in ("install", "check", "upgrade", "env"):
        s = sub.add_parser(name)
        s.add_argument("stack_file")
        s.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                       help="a value for a token in the stack file: org, project, region, cluster")
    s = sub.choices["env"]
    s.add_argument("--env-file", required=True, help="the gojira.env to write (created from the example if absent)")
    s.add_argument("--gojira-repo", default="", help="a Gojira checkout; its gojira.env.example starts a new file")
    s = sub.choices["install"]
    s.add_argument("--env-file", default="", help="the install's gojira.env (a template naming the GitHub App reads it)")
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
    values: dict[str, str] = {}
    for item in a.set:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            print(f"--set takes NAME=VALUE, got {item!r}", file=sys.stderr)
            return 2
        values[key.strip()] = value.strip()
    try:
        stack = Stack.load(path, values)
    except Exception as exc:  # pydantic / yaml / a token without a value: one sentence, no traceback
        print(f"{path}: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 2
    if a.cmd == "check":
        print(f"{path}: {len(stack.apps)} apps, in order: " + ", ".join(x.name for x in stack.apps))
        return 0
    if a.cmd == "env":
        return _write_env(stack, values, Path(a.env_file).expanduser(), a.gojira_repo)

    env_file = Path(a.env_file).expanduser() if getattr(a, "env_file", "") else None
    target = Target(stack_file=path, repos=Path(a.repos).expanduser() if getattr(a, "repos", "") else None,
                    cbl=getattr(a, "cbl", ""), cabal_port=getattr(a, "cabal_port", 17497),
                    values=values, env_file=env_file)
    try:
        report = perform(target, a.cmd, a.apps, merge_setup_prs=a.merge_setup_prs,
                         yes=getattr(a, "yes", False))
    except ToolError as exc:
        print(f"\n{exc}" if str(exc).startswith("cannot reach Gojira") else f"\nstopped: {exc}", file=sys.stderr)
        return 1
    print(f"\ndone: {len(report.done)} steps"
          + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))
    return 0


def _write_env(stack, values: dict[str, str], env_file: Path, gojira_repo: str) -> int:
    """`stack env`: Gojira's values file from the four and the stack file (stack.site).
    Prints the knobs it wrote; none of them is secret."""
    from stack.clients import GhCLI, ToolError
    from stack.site import TOKENS, env_values, write_env

    missing = [t for t in TOKENS if not values.get(t)]
    if missing:
        print("stack env needs every one of the four: --set " + " --set ".join(f"{t}=…" for t in missing), file=sys.stderr)
        return 2
    example = Path(gojira_repo).expanduser() / "gojira.env.example" if gojira_repo else None
    example_text = example.read_text() if example is not None and example.exists() else ""
    if not env_file.exists() and not example_text:
        print(f"{env_file} does not exist yet and no --gojira-repo was given to start it from "
              "(its gojira.env.example keeps a doc above every knob)", file=sys.stderr)
        return 2
    try:
        derived = env_values(stack, values, GhCLI())
        write_env(env_file, derived, example_text)
    except ToolError as exc:
        print(f"\nstopped: {exc}", file=sys.stderr)
        return 1
    print(f"{env_file}:")
    for k, v in derived.items():
        print(f"  {k}={v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
