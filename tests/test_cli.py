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


def test_cbl_opens_its_tunnel_on_demand_and_reopens_a_dead_one(tmp_path):
    """The forward cannot exist at startup (no Service yet) and dies when the
    pod restarts; each call checks the port and (re)opens the forward."""
    import socket
    import subprocess
    import sys

    from stack.clients import CblCLI

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()  # nothing listens until the "forward" runs
    opened = []

    def forward():
        opened.append(1)
        # a stand-in for kubectl port-forward: a child that listens on the port
        return subprocess.Popen([sys.executable, "-c",
                                 f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(60)"])

    cbl = CblCLI("/bin/true", f"http://127.0.0.1:{port}", str(tmp_path / "home"), forward=forward)
    cbl.ensure_tunnel(timeout=10)
    assert opened == [1]
    cbl.ensure_tunnel(timeout=10)          # still up: no second forward
    assert opened == [1]
    cbl._pf.kill()                         # the pod restarted
    cbl._pf.wait()
    cbl.ensure_tunnel(timeout=10)
    assert opened == [1, 1]
    cbl.close()
