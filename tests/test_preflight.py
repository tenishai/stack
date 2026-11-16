"""Preflight names what is missing, in the operator's words, and grants nothing."""

import os
import stat
import sys

from stack.clients import AppRow
from stack.model import Stack
from stack.preflight import check_cluster, check_files, check_github, check_tools, preflight
from stack.runs import Target
from tests.fakes import FakeGitHub, FakeGojira, FakeKubectl


def _stack(tmp_path, text):
    path = tmp_path / "stack.yaml"
    path.write_text("cluster: {name: c, gcp_project: p, location: us-central1}\n" + text)
    return Stack.load(path), Target(stack_file=path, repos=tmp_path / "repos")


def _by(checks, what):
    return next(c for c in checks if c.what == what)


def test_every_named_file_must_exist(tmp_path):
    (tmp_path / "ident.toml").write_text("x")
    (tmp_path / "es.yaml").write_text("x")
    (tmp_path / "repos" / "kobo" / "k8s" / "cluster").mkdir(parents=True)
    (tmp_path / "repos" / "kobo" / "k8s" / "cluster" / "rbac.yaml").write_text("x")
    stack, target = _stack(tmp_path, """
apps:
  - name: cabal
    repo: o/cabal
    configmaps: {cabal-config: {identity.toml: file:ident.toml}}
    secrets: {s: {pem: "file:~/nowhere/app.pem"}}
  - name: kobo
    repo: o/kobo
    image: r/kobo:v1
    deploy_repo: o/kobo-deploy
    cluster_objects: [k8s/cluster/rbac.yaml]
    base_files: [es.yaml, missing-es.yaml]
""")
    checks = check_files(stack, target)
    assert _by(checks, "cabal: file:ident.toml").status == "ok"
    missing = _by(checks, "cabal: file:~/nowhere/app.pem")
    assert missing.status == "fail" and "no such file" in missing.detail and "app.pem" in missing.detail
    assert _by(checks, "kobo: k8s/cluster/rbac.yaml").status == "ok"
    assert _by(checks, "kobo: es.yaml").status == "ok"
    assert _by(checks, "kobo: missing-es.yaml").status == "fail"


def test_tools_are_asked_for_only_when_the_stack_needs_them(tmp_path):
    stack, target = _stack(tmp_path, "apps:\n  - {name: litellm, repo: o/litellm}\n")
    checks = check_tools(stack, target, which=lambda t: f"/usr/bin/{t}" if t != "crane" else None)
    assert [c.what for c in checks] == ["kubectl", "gh"]           # no image app: crane not asked for
    stack, target = _stack(tmp_path, """
apps:
  - {name: cabal, repo: o/cabal, bootstrap: cabal}
  - {name: kobo, repo: o/kobo, image: r/kobo:v1, deploy_repo: o/kobo-deploy}
""")
    checks = check_tools(stack, target, which=lambda t: f"/usr/bin/{t}" if t != "crane" else None)
    assert _by(checks, "crane").status == "fail" and "brew install crane" in _by(checks, "crane").detail
    assert "cbl" in _by(checks, "cbl").detail and _by(checks, "cbl").status == "fail"
    target.cbl = str(tmp_path / "cbl")
    assert _by(check_tools(stack, target, which=lambda t: t), "cbl").detail.startswith("no such file")
    (tmp_path / "cbl").write_text("")
    assert _by(check_tools(stack, target, which=lambda t: t), "cbl").status == "ok"


def test_github_names_the_sso_gap_and_the_repos_it_cannot_see(tmp_path):
    stack, _ = _stack(tmp_path, """
apps:
  - {name: litellm, repo: tenishai/litellm}
  - {name: kobo, repo: tenishai/kobo, image: r/kobo:v1, deploy_repo: acme/infra-kobo-deploy}
""")
    gh = FakeGitHub(repos={"tenishai/litellm": {}})
    gh.org_denied["acme"] = "your gh token has no SSO grant for acme: Configure SSO -> Authorize"
    checks = check_github(stack, gh)
    assert _by(checks, "gh login").detail == "logged in as tester"
    assert _by(checks, "gh: tenishai").status == "ok"
    assert _by(checks, "gh: acme").status == "fail" and "SSO grant" in _by(checks, "gh: acme").detail
    assert _by(checks, "litellm: tenishai/litellm").status == "ok"
    deploy = _by(checks, "kobo: acme/infra-kobo-deploy")
    assert deploy.status == "warn" and "Install makes it" in deploy.detail
    gh.repos.clear()
    assert _by(check_github(stack, gh), "litellm: tenishai/litellm").status == "fail"
    gh.user = ""
    assert [c.status for c in check_github(stack, gh)] == ["fail"]


def test_cluster_rows_say_what_each_app_needs_next(tmp_path):
    stack, _ = _stack(tmp_path, """
apps:
  - {name: litellm, repo: o/litellm}
  - {name: cabal, repo: o/cabal}
  - {name: kobo, repo: o/kobo, image: "r/kobo@sha256:" + "b" * 64, deploy_repo: o/kobo-deploy}
  - {name: bab, repo: o/bab, image: "r/bab@sha256:" + "c" * 64, deploy_repo: o/bab-deploy}
""".replace('"r/kobo@sha256:" + "b" * 64', "r/kobo@sha256:" + "b" * 64)
      .replace('"r/bab@sha256:" + "c" * 64', "r/bab@sha256:" + "c" * 64))
    gojira = FakeGojira(apps=[
        AppRow(1, "cabal", "awaiting_pr", "https://github.com/o/cabal/pull/9"),
        AppRow(2, "kobo", "active"), AppRow(3, "bab", "failed"),
    ])
    kubectl = FakeKubectl(ready={"cabal": (1, 2), "bab": (0, 0)},
                          images={"kobo": "r/kobo:v1@sha256:" + "a" * 64, "cabal": "r/cabal:dev"})
    kubectl.images["bab"] = "r/bab@sha256:" + "c" * 64
    checks = check_cluster(stack, kubectl, gojira)
    assert _by(checks, "litellm: Gojira").status == "warn" and "Install" in _by(checks, "litellm: Gojira").detail
    assert "pull/9" in _by(checks, "cabal: Gojira").detail
    assert _by(checks, "cabal: pods").status == "warn" and "1/2" in _by(checks, "cabal: pods").detail
    assert _by(checks, "cabal: image").detail == "runs cabal:dev"   # built from source: shown, not judged
    assert _by(checks, "kobo: Gojira").status == "ok"
    pin = _by(checks, "kobo: image")
    assert pin.status == "warn" and "Upgrade" in pin.detail and "sha256:aaaaaaaaaaaa" in pin.detail
    assert _by(checks, "bab: Gojira").status == "fail"
    assert _by(checks, "bab: pods").status == "warn" and "none" in _by(checks, "bab: pods").detail
    assert _by(checks, "bab: image").status == "ok"


def test_the_whole_preflight_turns_a_strangers_shell_into_rows_not_tracebacks(tmp_path, monkeypatch):
    """kubectl on a context without Gojira, gh not logged in, no crane: three
    rows that say so, in seconds, with nothing on the cluster touched."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool, body in (("kubectl", 'case "$*" in *current-context*) echo elsewhere;; esac\nexit 1\n'),
                       ("gh", "exit 1\n")):
        f = bindir / tool
        f.write_text("#!/usr/bin/env bash\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.path.dirname(sys.executable)}")
    stack, target = _stack(tmp_path, """
apps:
  - {name: kobo, repo: o/kobo, image: r/kobo:v1, deploy_repo: o/kobo-deploy}
""")
    checks = preflight(stack, target)
    assert _by(checks, "kubectl").status == "ok"
    assert _by(checks, "crane").status == "fail"
    assert _by(checks, "gh login").status == "fail" and "not logged in" in _by(checks, "gh login").detail
    gojira = _by(checks, "Gojira")
    assert gojira.status == "fail" and "has no namespace 'gojira'" in gojira.detail and "KUBECONFIG" in gojira.detail
    assert not any(c.what.startswith("kobo: Gojira") for c in checks)
