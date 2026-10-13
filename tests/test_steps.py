"""The installer against fakes: the dogfood's rebuild, step by step, twice."""

from pathlib import Path

import pytest

from stack.clients import ToolError
from stack.model import Stack
from stack.steps import Clients, Installer, Options
from tests.fakes import FakeCabal, FakeGitHub, FakeGojira, FakeKubectl

ROOT = Path(__file__).parent.parent


def _stack(tmp_path, apps):
    return Stack.model_validate({
        "cluster": {"name": "gojira-sandbox", "gcp_project": "proj", "location": "us-central1"},
        "apps": apps,
    })


@pytest.fixture
def home(tmp_path):
    (tmp_path / "litellm-master-key").write_text("sk-lite\n")
    (tmp_path / "kobo.tmpl").write_text("gojira_token: \"{gojira_token}\"\norg: tenishai\n")
    repos = tmp_path / "repos" / "kobo" / "k8s" / "cluster"
    repos.mkdir(parents=True)
    (repos / "rbac.yaml").write_text("kind: ClusterRole\n")
    return tmp_path


def _run(tmp_path, stack, *, gojira=None, kubectl=None, github=None, cabal=None, **opts):
    clients = Clients(gojira=gojira or FakeGojira(), kubectl=kubectl or FakeKubectl(),
                      github=github or FakeGitHub(), cabal=cabal)
    lines = []
    o = Options(stack_dir=tmp_path, repo_root=tmp_path / "repos", poll_seconds=0,
                pr_wait_seconds=0.5, rollout_seconds=0.5, bootstrap_seconds=0.5,
                say=lines.append, **opts)
    inst = Installer(stack, clients, o)
    return inst, inst.install(), lines


def test_the_dogfood_order_from_nothing(home):
    """litellm → cabal (bootstrap) → liminal → kobo, every Secret from its
    source, tokens minted on the cabal the run just brought up, the operator's
    merges auto for this first install."""
    stack = _stack(home, [
        {"name": "litellm", "repo": "o/litellm",
         "secrets": {"litellm-secrets": {"LITELLM_MASTER_KEY": f"file:{home}/litellm-master-key"}}},
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal",
         "secrets": {"cabal-secrets": {"CABAL_MASTER_KEY": "generate:32",
                                       "LITELLM_MASTER_KEY": f"file:{home}/litellm-master-key"}}},
        {"name": "liminal", "repo": "o/liminal",
         "secrets": {"liminal-secrets": {"LIMINAL_CABAL_TOKEN": "cabal-token:service:liminal:secrets-fetch"}}},
        {"name": "kobo", "repo": "o/kobo", "cluster_objects": ["k8s/cluster/rbac.yaml"],
         "secrets": {"kobo-config": {"config.yaml": f"template:{home}/kobo.tmpl"},
                     "kobo-cabal": {"CABAL_OPERATOR_TOKEN": "cabal-token:service:kobo:operator"}}},
    ])
    gojira, kubectl, github, cabal = FakeGojira(), FakeKubectl(), FakeGitHub(), FakeCabal()
    inst, report, lines = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github, cabal=cabal,
                               merge_setup_prs=True)
    assert gojira.calls[0] == "register:gojira-sandbox"
    assert [c for c in gojira.calls if c.startswith("connect")] == [
        "connect:litellm:auto=True", "connect:cabal:auto=True", "connect:liminal:auto=True", "connect:kobo:auto=True"]
    assert kubectl.secrets[("litellm", "litellm-secrets")] == {"LITELLM_MASTER_KEY": "sk-lite"}
    assert len(kubectl.secrets[("cabal", "cabal-secrets")]["CABAL_MASTER_KEY"]) == 44  # 32 bytes, base64
    assert cabal.logged_in == "ABC123"
    assert cabal.minted == [("service:liminal", "secrets-fetch", 90), ("service:kobo", "operator", 90)]
    assert kubectl.secrets[("kobo", "kobo-config")]["config.yaml"].startswith('gojira_token: "tok-123"')
    assert kubectl.applied == [str(home / "repos" / "kobo" / "k8s" / "cluster" / "rbac.yaml")]
    assert [c for c in github.calls if c.startswith("merge")] == [f"merge:https://github.com/o/{a}/pull/1"
                                                                  for a in ("litellm", "cabal", "liminal", "kobo")]
    assert report.waited_on == []
    assert "kobo: 1/1 pods ready" in "\n".join(lines)


def test_a_second_run_changes_nothing(home):
    stack = _stack(home, [
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal",
         "secrets": {"cabal-secrets": {"CABAL_MASTER_KEY": "generate:32"}}},
    ])
    gojira, kubectl, github, cabal = FakeGojira(), FakeKubectl(), FakeGitHub(), FakeCabal()
    _run(home, stack, gojira=gojira, kubectl=kubectl, github=github, cabal=cabal, merge_setup_prs=True)
    key = kubectl.secrets[("cabal", "cabal-secrets")]["CABAL_MASTER_KEY"]
    for fake in (kubectl, gojira, github):
        fake.calls.clear()
    _, report, lines = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github, cabal=cabal,
                            merge_setup_prs=True)
    assert kubectl.calls == [] and github.calls == []
    assert [c for c in gojira.calls if c.startswith("connect")] == []
    assert kubectl.secrets[("cabal", "cabal-secrets")]["CABAL_MASTER_KEY"] == key   # not rotated
    assert "cabal: secret cabal-secrets exists" in "\n".join(lines)
    # the generated value lives in a 600 file, not in the report
    kept = next((home / ".generated").iterdir())
    assert oct(kept.stat().st_mode)[-3:] == "600" and key not in "\n".join(lines)


def test_without_auto_merge_it_waits_and_names_the_pr(home):
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    github = FakeGitHub()
    with pytest.raises(ToolError, match="still open"):
        _run(home, stack, github=github)
    assert github.calls == []


def test_a_merged_pr_is_synced_and_an_empty_namespace_is_deployed(home):
    """A reconnect whose render matches comes back active with no pods
    (tenishai/gojira#75): the installer dispatches the workflow."""
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    gojira = FakeGojira()
    gojira.connect_outcome["litellm"] = ("active", "")
    kubectl = FakeKubectl(ready={"litellm": (0, 0)})
    github = FakeGitHub(runs={"o/litellm": ("completed", "success")})

    # pods appear after the dispatch
    real = github.dispatch

    def dispatch(repo, workflow):
        real(repo, workflow)
        kubectl.ready["litellm"] = (1, 1)
    github.dispatch = dispatch
    _, report, _ = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert "dispatch:o/litellm:gojira-ci.yaml" in github.calls
    assert "litellm: deploy dispatched (namespace was empty)" in report.done


def test_a_failed_provision_is_tried_once_more_then_stops_the_run_by_name(home):
    """Seconds after an install Google is still catching up (a WIF provider
    the purge soft-deleted, gojira#117): the second provision is the one
    that works. Two failures are a real failure, and the next app never
    starts."""
    stack = _stack(home, [{"name": "kobo", "repo": "o/kobo"}, {"name": "after", "repo": "o/after"}])
    gojira = FakeGojira()
    gojira.connect_outcome["kobo"] = ("failed", "")
    gojira.provision_outcomes[1] = ["active"]
    _, report, _ = _run(home, stack, gojira=gojira, iam_settle_seconds=0, merge_setup_prs=True)
    assert "kobo: provisioned on the second try" in report.done
    assert gojira.calls.count("provision:1") == 1

    gojira = FakeGojira()
    gojira.connect_outcome["kobo"] = ("failed", "")
    gojira.provision_outcomes[1] = ["failed"]
    with pytest.raises(ToolError, match="kobo: provision failed twice"):
        _run(home, stack, gojira=gojira, iam_settle_seconds=0)
    assert not any(c.startswith("connect:after") for c in gojira.calls)


def test_a_cabal_token_before_cabal_is_up_is_refused(home):
    stack = _stack(home, [
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal"},
        {"name": "liminal", "repo": "o/liminal",
         "secrets": {"liminal-secrets": {"T": "cabal-token:service:liminal:secrets-fetch"}}},
    ])
    with pytest.raises(ToolError, match="no cabal client"):
        _run(home, stack, cabal=None, merge_setup_prs=True)


def test_the_dogfood_example_drives_the_installer(home, monkeypatch):
    """The checked-in example resolves every source against fakes: the file
    is not just valid, it is runnable."""
    stack = Stack.load(ROOT / "examples" / "dogfood.yaml")
    for app in stack.apps:
        for keys in list(app.secrets.values()) + list(app.configmaps.values()):
            for s in keys.values():
                if s.kind in ("file", "template"):
                    monkeypatch.setattr(type(s), "path", property(lambda self, h=home: h / "litellm-master-key"))
    kubectl = FakeKubectl()
    repos = home / "repos" / "kobo" / "k8s" / "cluster"
    assert (repos / "rbac.yaml").exists()
    _, report, _ = _run(home, stack, kubectl=kubectl, cabal=FakeCabal(), merge_setup_prs=True)
    assert {ns for ns, _ in kubectl.secrets} == {"litellm", "cabal", "liminal", "kobo"}
    assert any("kobo: 1/1 pods ready" in d for d in report.done)


def test_a_rerun_on_a_live_stack_does_not_look_for_a_bootstrap_code(home):
    """The code is printed only at startup; when every later token Secret
    already exists there is nothing to mint, so the rerun skips the login."""
    stack = _stack(home, [
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal"},
        {"name": "liminal", "repo": "o/liminal",
         "secrets": {"liminal-secrets": {"T": "cabal-token:service:liminal:secrets-fetch"}}},
    ])
    kubectl = FakeKubectl(log_code=None)
    kubectl.secrets[("liminal", "liminal-secrets")] = {"T": "old"}
    cabal = FakeCabal()
    _, report, _ = _run(home, stack, kubectl=kubectl, cabal=cabal, merge_setup_prs=True)
    assert cabal.logged_in is None and cabal.minted == []
    assert "cabal: no tokens left to mint, bootstrap skipped" in report.done


def test_a_deploy_that_fails_on_a_fresh_identity_is_dispatched_once_more(home):
    """The first stranger run (2026-10-08): CI four seconds after the
    identity was made failed on impersonation; the second try, a minute
    later, is the one that works."""
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    gojira = FakeGojira()
    gojira.connect_outcome["litellm"] = ("active", "")
    kubectl = FakeKubectl(ready={"litellm": (0, 0)})
    github = FakeGitHub()
    github.outcomes["o/litellm"] = ["failure", "success"]
    real = github.dispatch

    def dispatch(repo, workflow):
        real(repo, workflow)
        if github.runs[repo][1] == "success":
            kubectl.ready["litellm"] = (1, 1)
    github.dispatch = dispatch
    _, report, lines = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github, iam_settle_seconds=0)
    assert github.calls.count("dispatch:o/litellm:gojira-ci.yaml") == 2
    assert "litellm: deploy dispatched again" in report.done
    assert any("trying once more" in ln for ln in lines)


def test_a_deploy_that_fails_twice_stops_the_run_with_the_run_to_read(home):
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    gojira = FakeGojira()
    gojira.connect_outcome["litellm"] = ("active", "")
    github = FakeGitHub()
    github.outcomes["o/litellm"] = ["failure", "failure"]
    with pytest.raises(ToolError, match="failed twice.*github.com/o/litellm/actions"):
        _run(home, stack, gojira=gojira, kubectl=FakeKubectl(ready={"litellm": (0, 0)}), github=github,
             iam_settle_seconds=0)
