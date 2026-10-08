"""The command line: a stranger's first run on a shared machine."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_check_runs_with_a_hostile_pythonpath(tmp_path):
    """A PYTHONPATH naming a directory with a broken `pydantic` must not reach
    the installer: it re-execs without it."""
    bad = tmp_path / "shadow" / "pydantic"
    bad.mkdir(parents=True)
    (bad / "__init__.py").write_text("raise ImportError('shadowed pydantic')\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "shadow")}
    env.pop("STACK_REEXEC", None)
    done = subprocess.run([sys.executable, "-m", "stack.cli", "check", str(ROOT / "examples" / "dogfood.yaml")],
                          capture_output=True, text=True, env=env, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert "4 apps, in order: litellm, cabal, liminal, kobo" in done.stdout
