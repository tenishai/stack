"""Slice 3 (criteria 2, 4): Check on an empty cluster says so plainly, and the
page offers *Install Gojira*, which runs `scripts/install.sh` from the profile's
Gojira checkout as a subprocess under the pinned env (never sourcing it into the
UI process). Every test here fails at 1e1f0b4."""

import stat
import time
from pathlib import Path

from fastapi.testclient import TestClient

from stack.preflight import Check
from stack.profiles import Profiles
from stack.runs import Runner
from stack.ui import make_app

SECRET = "SECRETVALUE123"
NS = "gojira"   # the Gojira namespace the fake cluster does or does not have


def _wait(run, seconds=10):
    deadline = time.monotonic() + seconds
    while run.finished is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert run.finished is not None, "the run never finished"


def _stack_file(tmp_path: Path) -> Path:
    f = tmp_path / "s.yaml"
    f.write_text(
        "cluster: {name: gojira-sandbox, gcp_project: vidios-qa, location: us-central1}\n"
        "apps:\n  - {name: kobo, repo: tenishai/gojira, deploy_repo: tenishai/kobo-deploy,\n"
        "     image: r/agent-images/kobo:1, source_image: ghcr.io/tenishai/kobo}\n")
    return f


def _fake_install(tmp_path: Path, *, rc: int = 0) -> Path:
    """A stand-in for Gojira's scripts/install.sh: echoes the values it saw,
    exits `rc`. install.sh reads the values from its sourced gojira.env, so the
    test proves pinning by the env it receives, not by parsing the file."""
    d = tmp_path / "gojira"
    s = d / "scripts"
    s.mkdir(parents=True, exist_ok=True)
    p = s / "install.sh"
    # like the real one: read the values by sourcing gojira.env from $GOJIRA_ENV.
    # `source` (not a subshell) so every value is parsed as a variable assignment;
    # nothing is ever *executed*, so even an odd value cannot leak onto the script's output
    p.write_text("#!/usr/bin/env bash\n"
                 "if [ -n \"${GOJIRA_ENV:-}\" ]; then set -a; source \"$GOJIRA_ENV\" 2>/dev/null || true; set +a; fi\n"
                 "echo \"install: PROJECT=${PROJECT:-} GOJIRA_ENV=${GOJIRA_ENV:-}\"\n"
                 f"exit {rc}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return d   # the checkout's root (its scripts/install.sh is the entry point)


class _NoProj:
    """The env-file page's gcloud stand-in: an account, no projects (slice 2's
    pick lists are not what this file is about)."""

    def __init__(self, **kw):
        pass

    def account(self):
        return "me@example.com"

    def projects(self):
        return []


def _k8s(ns_exists: bool = True, ready=(1, 1), *, version: str | None = "v1.3.1", dead: bool = False):
    """A kubectl-shaped fake. The version is where Gojira actually writes it:
    ConfigMap `gojira-install`/`GOJIRA_VERSION` (bootstrap.sh), NOT `gojira-version`
    /`version` — that one is never written. `dead` makes `namespace_exists` and
    `pods_ready` raise (an unreadable cluster), for the unknown state."""
    from stack.clients import ToolError
    from tests.fakes import FakeKubectl

    k = FakeKubectl(ready={NS: ready})
    if ns_exists:
        k.ns_set.add(NS)
        if version is not None:
            k.configmaps[(NS, "gojira-install")] = {"GOJIRA_VERSION": version}
    if dead:
        setattr(k, "namespace_exists", lambda ns: (_ for _ in ()).throw(ToolError("cluster unreadable")))
        setattr(k, "pods_ready", lambda ns: (_ for _ in ()).throw(ToolError("cluster unreadable")))
    return k


def _site(tmp_path, *, install: Path, kubectl, ns_exists: bool = True):
    profiles_path = tmp_path / "p.yaml"
    stack = _stack_file(tmp_path)
    env = tmp_path / "gojira.env"
    env.write_text("# install's values\nPROJECT=vidios-qa\nCLUSTER=gojira-sandbox\n")
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("")
    runner = Runner()

    def check_pf(stackf, target):
        # the cluster row the UI's Check shows for an app on the Gojira, in the
        # slice-3 words: an empty cluster is a plain "no Gojira yet", not a refusal
        return [Check("tools", "ok", "kubectl on PATH"),
                Check("kobo: Gojira", "ok" if ns_exists else "warn",
                      "active" if ns_exists else "no Gojira on this cluster yet")]

    app = make_app(profiles_path, runner,
                   perform=lambda *a, **k: (_ for _ in ()).throw(AssertionError("not the stack engine")),
                   preflight=check_pf,
                   kubectl=lambda *, env: kubectl, gcloud=lambda *, env: _NoProj())
    client = TestClient(app)
    client.runner, client.env, client.profiles_path = runner, env, profiles_path
    client.post("/profiles", data={"name": "prod", "stack_file": str(stack),
                                   "kubeconfig": str(kubeconfig), "env_file": str(env),
                                   "repos": str(tmp_path), "gojira_repo": str(install)})
    return client


# --- the profile page states the empty cluster plainly (criterion 2) ---


def test_check_on_an_empty_cluster_says_no_gojira_yet(tmp_path):
    """Criterion 2: on a cluster with no Gojira, the page states it in plain words
    and offers *Install Gojira* — a refusal is never the whole answer. Driven through
    the profile page, which is what an operator actually sees."""
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=False), ns_exists=False)
    page = site.get("/p/prod").text
    # the plain word, not a traceback, and the install offer follows from it
    assert "no Gojira on this cluster yet" in page
    assert "Traceback" not in page and "traceback" not in page
    # every other part of the page still rendered
    assert "prod" in page and "gojira-sandbox" in page


# --- the page offers Install Gojira only when nothing is installed yet ---


def test_page_offers_install_gojira_when_empty(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=False))
    page = site.get("/p/prod").text
    assert "Install Gojira" in page


def test_page_hides_install_gojira_when_installed(tmp_path):
    """Item 4(b): installed -> version + the same button, relabelled for what it
    does ("Run install.sh again") — make install from a newer checkout is how Gojira
    is upgraded, so the surface for it must stay."""
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=True), ns_exists=True)
    page = site.get("/p/prod").text
    # the page knows a Gojira is already there (its version, from gojira-install)
    assert "v1.3.1" in page
    assert "is on this cluster" in page
    # and it still offers the re-run button, labelled for what it does
    assert "Run install.sh again" in page
    assert "no Gojira on this cluster yet" not in page


# --- the refusals: no env file, no checkout, each naming which ---


def test_install_gojira_refuses_when_no_env_file(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=False))
    p = site.profiles_path
    book = Profiles.load(p)
    book.profiles[0].env_file = ""
    book.save(p)
    r = site.post("/p/prod/install-gojira", follow_redirects=False)
    assert r.status_code == 422
    assert "env file" in r.text


def test_install_gojira_refuses_when_no_checkout(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=False))
    p = site.profiles_path
    book = Profiles.load(p)
    # no checkout AND no repos dir to fall back to: genuinely nowhere to install from
    book.profiles[0].gojira_repo = ""
    book.profiles[0].repos = ""
    book.save(p)
    r = site.post("/p/prod/install-gojira", follow_redirects=False)
    assert r.status_code == 422
    assert "gojira_repo" in r.text or "Gojira checkout" in r.text


# --- the happy path: install.sh runs as a subprocess, lines on the page ---


def test_install_gojira_runs_install_sh_as_a_subprocess(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=False))
    r = site.post("/p/prod/install-gojira", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/runs/1"
    _wait(site.runner.runs[1])
    assert site.runner.runs[1].status == "done"
    page = site.get("/runs/1").text
    # the script's own line, with the values it saw, is on the run page
    assert "install:" in page and "GOJIRA_ENV=" in page
    # GOJIRA_ENV points at the profile's env file, and the values it read
    assert site.env.name in page and "PROJECT=vidios-qa" in page


def test_env_values_never_appear_in_the_run_lines(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=False))
    # the env file carries a secret value
    site.env.write_text(f"# install's values\nPROJECT=vidios-qa\nGITHUB_APP_PEM=-----BEGIN {SECRET}-----\n")
    site.post("/p/prod/install-gojira", follow_redirects=False)
    _wait(site.runner.runs[1])
    run = site.runner.runs[1]
    for line in run.lines:
        assert SECRET not in line.text
    # and not in the run page's HTML
    assert SECRET not in site.get("/runs/1").text


def test_install_gojira_refuses_when_install_sh_fails(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path, rc=1), kubectl=_k8s(ns_exists=False))
    site.post("/p/prod/install-gojira", follow_redirects=False)
    _wait(site.runner.runs[1])
    assert site.runner.runs[1].status == "failed"
    page = site.get("/runs/1").text
    assert "stopped:" in page


# --- the profile form carries the gojira_repo field ---


def test_profile_form_has_a_gojira_repo_field(tmp_path):
    stack = _stack_file(tmp_path)
    site = TestClient(make_app(tmp_path / "p.yaml", Runner(),
                               perform=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
                               preflight=lambda s, t: [], kubectl=lambda *, env: _k8s(),
                               gcloud=lambda *, env: _NoProj()))
    site.profiles_path = tmp_path / "p.yaml"
    site.post("/profiles", data={"name": "prod", "stack_file": str(stack)})
    page = site.get("/p/prod").text
    assert "gojira_repo" in page   # the settings form names the checkout


# --- done: the Gojira pod ready and /healthz answers with a version ---


def test_install_gojira_reports_the_go_jira_version_when_ready(tmp_path):
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=True, ready=(1, 1)))
    site.post("/p/prod/install-gojira", follow_redirects=False)
    _wait(site.runner.runs[1])
    page = site.get("/runs/1").text
    # criterion 4: the version, read where Gojira actually writes it
    # (gojira-install/GOJIRA_VERSION — not the never-written gojira-version)
    assert "v1.3.1" in page


def test_install_gojira_reads_the_version_from_healthz_when_the_configmap_is_absent(tmp_path, monkeypatch):
    """Ember 10 item 3 (pins the version *precedence*): the version's primary
    source is `/healthz` (the port-forwarded Gojira), not the ConfigMap. Here the
    ConfigMap is absent and the Gojira's `health()` carries the version, so the
    run's version must come from the client `open_gojira` returns. (The *wiring*
    — that the run calls `open_gojira` with a callable `say` and passes the client
    to the helper — is pinned by test_install_gojira_drives_the_real_open_gojira;
    this one stubs `open_gojira` to isolate the precedence.)"""
    import stack.runs as runs_mod
    from tests.fakes import FakeGojira

    fake_go = FakeGojira(version="v9.9.9")   # /healthz carries this
    monkeypatch.setattr(runs_mod, "open_gojira", lambda *a, **k: (_k8s(ns_exists=True, ready=(1, 1)), fake_go))
    # no ConfigMap (version=None), pods ready; the only version source is health()
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=True, ready=(1, 1), version=None))
    site.post("/p/prod/install-gojira", follow_redirects=False)
    _wait(site.runner.runs[1])
    page = site.get("/runs/1").text
    assert "v9.9.9" in page   # from health(), not the (absent) ConfigMap


def test_install_gojira_drives_the_real_open_gojira(tmp_path, monkeypatch):
    """Ember 11 item 1: the install run must go through the REAL `open_gojira`
    (patch only the `KubectlCLI`/`HttpGojira` classes it constructs, never the
    function itself). At 5e4e998 the run called `open_gojira(stack, target,
    say=run.add)`; `open_gojira` does `say(f"Gojira: context …")` with ONE arg but
    `Run.add` needs two — a `TypeError` (not a `ToolError`), which the run's
    `except ToolError` missed and the Runner's catch-all turned into a crash after
    "pods ready". With `say=run.say` the message lands and the call proceeds to the
    version. This pins the wiring; the healthz test above pins the precedence."""
    import stack.runs as runs_mod
    from tests.fakes import FakeGojira

    # patch the two classes open_gojira constructs; the function itself is real
    kc = _k8s(ns_exists=True, ready=(1, 1), version="v7.7.7")
    fake_go = FakeGojira(version="v7.7.7")
    monkeypatch.setattr(runs_mod, "KubectlCLI", lambda *a, **k: kc)
    monkeypatch.setattr(runs_mod, "HttpGojira", lambda *a, **k: fake_go)
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(ns_exists=True, ready=(1, 1), version="v7.7.7"))
    site.post("/p/prod/install-gojira", follow_redirects=False)
    _wait(site.runner.runs[1])
    run = site.runner.runs[1]
    assert run.status == "done"           # not "failed" (the 5e4e998 crash)
    page = site.get("/runs/1").text
    assert "Gojira v7.7.7" in page        # the version line
    assert "Gojira: context" in page      # open_gojira's say() landed via run.say


# --- unknown: a dead kubectl is neither empty nor installed (Ember 09 item 3) ---


def test_dead_kubectl_is_unknown_not_empty(tmp_path):
    """Item 3: an unreadable cluster (namespace_exists raises) is a third state —
    unknown. The page says it could not read the cluster and offers no button and
    no empty claim: it must not offer to install into a cluster it could not read."""
    site = _site(tmp_path, install=_fake_install(tmp_path), kubectl=_k8s(dead=True))
    page = site.get("/p/prod").text
    assert "Could not read this cluster" in page
    # neither the empty-state text nor an enabled install button (the action, not the phrase)
    assert "no Gojira on this cluster yet" not in page
    assert "/p/prod/install-gojira" not in page


# --- namespace-only: the namespace exists but nothing answers (Ember 09 item 4a) ---


def test_namespace_only_says_so_and_offers_install(tmp_path):
    """Item 4(a): namespace gojira present but no pods (a half-failed install leaves
    exactly this, as does any pre-v1.0.0 install). The page says so — "namespace
    gojira exists, nothing answers" — and offers the button; it shows no version."""
    site = _site(tmp_path, install=_fake_install(tmp_path),
                 kubectl=_k8s(ns_exists=True, ready=(0, 0), version=None), ns_exists=True)
    page = site.get("/p/prod").text
    assert "namespace gojira exists, pods not all ready" in page
    assert "Install Gojira" in page
    # no version claimed (the namespace is not a running Gojira)
    assert "is on this cluster" not in page
    assert "v1.3.1" not in page