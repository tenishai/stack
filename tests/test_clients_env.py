"""Every tool call runs in the environment the caller pins, not the shell's."""

import os
import stat

from stack.clients import GhCLI, KubectlCLI, tool_env


def _fake(bindir, name, body):
    f = bindir / name
    f.write_text("#!/usr/bin/env bash\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


def test_kubectl_and_gh_run_with_the_pinned_environment(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "seen"
    _fake(bindir, "kubectl", f'echo "kubectl KUBECONFIG=$KUBECONFIG ACCOUNT=$CLOUDSDK_CORE_ACCOUNT PP=$PYTHONPATH" >> {seen}\nexit 0\n')
    _fake(bindir, "gh", f'echo "gh KUBECONFIG=$KUBECONFIG" >> {seen}\necho tester\n')
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", "/someone/elses")
    monkeypatch.setenv("KUBECONFIG", "/the/shells")
    pinned = {"KUBECONFIG": "/the/profiles", "CLOUDSDK_CORE_ACCOUNT": "me@example.com"}
    assert KubectlCLI("ctx", env=pinned).namespace_exists("gojira")
    assert GhCLI(env=pinned).login() == "tester"
    lines = seen.read_text().splitlines()
    assert lines[0] == "kubectl KUBECONFIG=/the/profiles ACCOUNT=me@example.com PP="
    assert lines[1] == "gh KUBECONFIG=/the/profiles"
    # nothing pinned: the shell's own, still without PYTHONPATH
    assert tool_env()["KUBECONFIG"] == "/the/shells" and "PYTHONPATH" not in tool_env()
