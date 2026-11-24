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
  - {name: litellm, repo: tenishai/litellm-deploy}
  - {name: kobo, repo: tenishai/kobo, image: r/kobo:v1, deploy_repo: acme/infra-kobo-deploy}
""")
    gh = FakeGitHub(repos={"tenishai/litellm-deploy": {}})
    gh.org_denied["acme"] = "your gh token has no SSO grant for acme: Configure SSO -> Authorize"
    checks = check_github(stack, gh)
    assert _by(checks, "gh login").detail == "logged in as tester"
    assert _by(checks, "gh: tenishai").status == "ok"
    assert _by(checks, "gh: acme").status == "fail" and "SSO grant" in _by(checks, "gh: acme").detail
    assert _by(checks, "litellm: tenishai/litellm-deploy").status == "ok"
    deploy = _by(checks, "kobo: acme/infra-kobo-deploy")
    assert deploy.status == "warn" and "Install makes it" in deploy.detail
    gh.repos.clear()
    assert _by(check_github(stack, gh), "litellm: tenishai/litellm-deploy").status == "fail"
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


def test_an_empty_cluster_is_a_plain_word_and_every_row_runs(tmp_path, monkeypatch):
    """Criterion 2 (Ember 09): a reachable cluster with no gojira namespace is a
    plain warn ("no Gojira on this cluster yet"), not the refusal, and every other
    row still ran — the apps say "not installed yet — Install Gojira first". This is
    the case `preflight` used to refuse on and return early (no app rows).
    The fake kubectl answers `get ns --no-headers` with a list that lacks gojira,
    so the cluster is reachable but empty."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # reachable cluster: `get ns` lists (no gojira); `get ns gojira` not-found; current-context works
    kubectl_body = """
if [[ "$*" == *"--no-headers"* ]]; then echo "default kube-system"; exit 0; fi
case "$*" in *current-context*) echo fake-context;; esac
exit 1
"""
    (bindir / "kubectl").write_text("#!/bin/bash\n" + kubectl_body)
    (bindir / "kubectl").chmod((bindir / "kubectl").stat().st_mode | stat.S_IEXEC)
    (bindir / "gh").write_text("#!/bin/bash\nexit 1\n")
    (bindir / "gh").chmod((bindir / "gh").stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.path.dirname(sys.executable)}")
    stack, target = _stack(tmp_path, """
apps:
  - {name: kobo, repo: o/kobo, image: r/kobo:v1, deploy_repo: o/kobo-deploy}
""")
    checks = preflight(stack, target)
    by = {c.what: c for c in checks}
    # the Gojira row is a plain warn, not the refusal
    assert by["Gojira"].status == "warn" and "no Gojira on this cluster yet" in by["Gojira"].detail
    # every other row still ran
    assert by["kubectl"].status == "ok"
    # the app row says not installed yet — Install Gojira first (the new wording)
    kobo = _by(checks, "kobo: Gojira")
    assert kobo.status == "warn" and "not installed yet" in kobo.detail and "Install Gojira first" in kobo.detail


def test_an_unconnected_app_on_an_installed_go_jira_keeps_the_old_words(tmp_path):
    """Ember 10 item 1: `check_cluster` says `not connected; Install does it` for
    an app with no Gojira row, whatever cluster it runs against — that is the
    common case (a Gojira is there, this app is not on it: Install *the app*, not
    Gojira). The `Install Gojira first` words belong only to the empty-cluster
    branch of `preflight`, which supplies them itself. A prod-the-day-someone-adds
    a-sixth-app cluster: namespace present, a Gojira with no apps, an app not on it."""
    from tests.fakes import FakeGojira, FakeKubectl
    stack, target = _stack(tmp_path, """
apps:
  - {name: kobo, repo: o/kobo, image: r/kobo:v1, deploy_repo: o/kobo-deploy}
""")
    kc = FakeKubectl()
    kc.ns_set.add(stack.gojira.namespace)   # the namespace is there (Gojira is installed)
    gojira = FakeGojira(apps=[])           # ...but this app is not on it
    checks = check_cluster(stack, kc, gojira)
    kobo = _by(checks, "kobo: Gojira")
    assert kobo.status == "warn"
    assert "not connected" in kobo.detail
    assert "Install Gojira" not in kobo.detail   # the wrong action in the common case


def test_an_empty_fake_cluster_warns_not_refuses(monkeypatch, tmp_path):
    """Ember 10 item 2: `preflight` on an empty cluster, through the real code path
    with `KubectlCLI` patched to a `FakeKubectl` that has no gojira namespace.
    This calls `KubectlCLI.namespaces()` — the method the fake's old
    `self.namespaces` set shadowed (`TypeError: 'set' object is not callable` at
    811e21f), so it is the test that proves the rename, not the shell-kubectl
    second test. The row is a warn, not a refusal.
    Note on the negative proof: this passes at 811e21f *if HEAD's fakes.py ships
    with it* (the rename is the fix, so the fake has to be the old one for the
    proof to fail) — copy the test file but keep the parent's fakes.py to see it
    fail with the TypeError."""
    import stack.preflight as pf

    empty = FakeKubectl()   # no namespaces at all
    monkeypatch.setattr(pf, "KubectlCLI", lambda *a, **k: empty)
    # a real gh not logged in is fine; this test is about the cluster row
    stack, target = _stack(tmp_path, """
apps:
  - {name: kobo, repo: o/kobo, image: r/kobo:v1, deploy_repo: o/kobo-deploy}
""")
    checks = preflight(stack, target)
    gojira = _by(checks, "Gojira")
    assert gojira.status == "warn" and "no Gojira on this cluster yet" in gojira.detail
    # the app row ran and carries the empty-cluster words
    kobo = _by(checks, "kobo: Gojira")
    assert kobo.status == "warn" and "Install Gojira first" in kobo.detail


def test_kubectlcli_namespaces_returns_str_not_bytes(tmp_path, monkeypatch):
    """Ember 10 item 0 / 11 item 0: the real `KubectlCLI.namespaces()` must return
    a `list[str]`, not a `list[bytes]` — `subprocess.run` without `text=True`
    returns `r.stdout` as bytes, so every entry was `b"gojira"` and
    `stack.gojira.namespace not in listed` was always true (a cluster that runs
    Gojira reported as empty). A kubectl on PATH that lists three namespaces:
    the gojira one is recognised as present (str, not bytes)."""
    from stack.clients import KubectlCLI

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # real `get ns --no-headers` lists one namespace per line
    script = "#!/bin/bash\n"
    script += 'if [[ "$*" == *"--no-headers"* ]]; then '
    script += "printf 'default\\ngojira\\nkube-system\\n'\n"
    script += "exit 0; fi\nexit 0\n"
    (bindir / "kubectl").write_text(script)
    (bindir / "kubectl").chmod((bindir / "kubectl").stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.path.dirname(sys.executable)}")
    k = KubectlCLI(env={})
    ns = k.namespaces()
    assert ns is not None
    assert all(isinstance(n, str) for n in ns)   # the bytes bug
    assert ns == ["default", "gojira", "kube-system"]


def test_preflight_reaches_open_gojira_when_the_cluster_lists_go_jira(tmp_path, monkeypatch):
    """Ember 10 item 0 / 11 item 0: with `text=True`, a cluster that lists the
    gojira namespace is NOT the empty case, so `preflight` reaches `open_gojira`
    (the Gojira row is whatever `open_gojira` says, not the warn). Here the
    listing has gojira, so the warn row must be absent; `open_gojira` is patched
    to a known-good Gojira so the row is the ok row with its version."""
    import stack.preflight as pf

    kc = FakeKubectl(ready={"gojira": (1, 1)})
    kc.ns_set.add("gojira")                       # the cluster lists gojira
    monkeypatch.setattr(pf, "KubectlCLI", lambda *a, **k: kc)
    monkeypatch.setattr(pf, "open_gojira", lambda *a, **k: (kc, FakeGojira(version="v1.3.1")))
    stack, target = _stack(tmp_path, """
apps:
  - {name: kobo, repo: o/kobo, image: r/kobo:v1, deploy_repo: o/kobo-deploy}
""")
    checks = preflight(stack, target)
    gojira = _by(checks, "Gojira")
    # reached open_gojira: an ok row naming the context (NOT the empty-case warn)
    assert gojira.status == "ok" and "context" in gojira.detail
    assert "no Gojira on this cluster yet" not in gojira.detail
    # the app row ran (it is not the empty-cluster "Install Gojira first" warn)
    kobo = _by(checks, "kobo: Gojira")
    assert "Install Gojira first" not in kobo.detail
