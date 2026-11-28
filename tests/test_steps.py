"""The installer against fakes: the dogfood's rebuild, step by step, twice."""

import time
from pathlib import Path

import pytest
import yaml

from stack.clients import AppRow, ToolError
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


def test_a_named_generated_value_is_shared_and_relative_paths_follow_the_file(home):
    """litellm and cabal must hold the same LITELLM_MASTER_KEY, so one named
    `generate:` serves both; and a `file:` without a leading slash is read next
    to the stack file, so the file and its configs travel together."""
    (home / "configs").mkdir()
    (home / "configs" / "identity.toml").write_text("[cabal]\nport = 7400\n")
    stack = _stack(home, [
        {"name": "litellm", "repo": "o/litellm",
         "secrets": {"litellm-secrets": {"LITELLM_MASTER_KEY": "generate:32:litellm-master-key"}}},
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal",
         "configmaps": {"cabal-config": {"identity.toml": "file:configs/identity.toml"}},
         "secrets": {"cabal-secrets": {"CABAL_MASTER_KEY": "generate:32",
                                       "LITELLM_MASTER_KEY": "generate:32:litellm-master-key"}}},
    ])
    kubectl = FakeKubectl()
    _run(home, stack, kubectl=kubectl, cabal=FakeCabal(), merge_setup_prs=True)
    shared = kubectl.secrets[("litellm", "litellm-secrets")]["LITELLM_MASTER_KEY"]
    assert kubectl.secrets[("cabal", "cabal-secrets")]["LITELLM_MASTER_KEY"] == shared
    assert kubectl.secrets[("cabal", "cabal-secrets")]["CABAL_MASTER_KEY"] != shared
    assert kubectl.configmaps[("cabal", "cabal-config")]["identity.toml"] == "[cabal]\nport = 7400"
    assert sorted(p.name for p in (home / ".generated").iterdir()) == ["cabal-32", "litellm-master-key"]


def test_without_auto_merge_it_waits_and_names_the_pr(home):
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    github = FakeGitHub()
    with pytest.raises(ToolError, match="still open"):
        _run(home, stack, github=github)
    assert github.calls == []


def test_a_merge_the_rules_refuse_waits_for_the_approval_then_continues(home):
    """GitHub's rules on the repo want a review: the installer says so with the
    link, keeps waiting, tries its own merge again on every poll, and continues
    the moment the merge goes through — the person only approves. At 6126f2a the
    refusal was a ToolError straight out of merge_pr and the run stopped."""
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    github = FakeGitHub(runs={"o/litellm": ("completed", "success")})
    github.refuse_merges = 2                    # two polls' worth of "needs a review"
    kubectl = FakeKubectl(ready={"litellm": (1, 1)})
    _, report, lines = _run(home, stack, github=github, kubectl=kubectl, merge_setup_prs=True)
    refused = [c for c in github.calls if c.startswith("merge-refused:")]
    merged = [c for c in github.calls if c.startswith("merge:")]
    assert len(refused) == 2 and len(merged) == 1
    url = merged[0].split(":", 1)[1]
    assert any("want a review before " + url in ln and "approve it and the install continues" in ln
               for ln in lines)
    assert url in report.waited_on
    assert any(n.startswith("litellm: setup PR merged, synced") for n in report.done)


def test_a_merge_call_that_fails_is_still_a_stop(home):
    """Only a rule refusal waits; gh failing to reach GitHub stops the run with
    gh's own sentence. It guards the narrowing: at 6126f2a it fails only because
    the old fake had no merge_error, not because the stop was ever missing."""
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    github = FakeGitHub()
    github.merge_error = ToolError("error connecting to api.github.com: connection reset")
    with pytest.raises(ToolError, match="connection reset"):
        _run(home, stack, github=github, merge_setup_prs=True)


def test_a_failed_provision_names_the_app_page(home):
    """"See the app's page on Gojira" with no address sent a person looking; the
    stop names the page when the client knows where Gojira is."""
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}])
    gojira = FakeGojira()
    gojira.url = "http://127.0.0.1:18097"
    gojira.connect_outcome["litellm"] = ("failed", "")
    gojira.provision_outcomes[1] = ["failed"]          # the one retry fails too
    with pytest.raises(ToolError, match=r"provision failed twice; see http://127\.0\.0\.1:18097/apps/1"):
        _run(home, stack, gojira=gojira, iam_settle_seconds=0)


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


def test_an_app_asked_for_by_name_is_provisioned_again_even_unchanged(home):
    """`stack install file bab` on a live, unchanged bab: the contract on the
    repo may carry what only a provision acts on (2026-11-09, an identity:
    line), so a named app is converged. The whole-file install still leaves
    an active, unchanged app alone."""
    stack = _stack(home, [{"name": "bab", "repo": "o/bab"}, {"name": "kobo", "repo": "o/kobo"}])
    rows = [AppRow(id=1, name="bab", status="active"), AppRow(id=2, name="kobo", status="active")]
    gojira = FakeGojira(apps=rows)
    inst = Installer(stack, Clients(gojira=gojira, kubectl=FakeKubectl(), github=FakeGitHub()),
                     Options(stack_dir=home, repo_root=home / "repos", poll_seconds=0, say=lambda _: None))
    inst.install(["bab"])
    assert [c for c in gojira.calls if c.startswith("provision")] == ["provision:1"]
    assert "bab: provision pressed again (asked for by name)" in inst.report.done
    gojira.calls.clear()
    inst.install()
    assert not any(c.startswith("provision") for c in gojira.calls)


def test_pods_present_but_a_failed_last_deploy_is_dispatched_again(home):
    """An identity added to a live app: the provision set the repo variable
    and changed no file, so nothing ran the workflow whose last run had
    failed on that very variable (2026-11-09). The installer runs it."""
    stack = _stack(home, [{"name": "bab", "repo": "o/bab"}])
    gojira = FakeGojira()
    gojira.connect_outcome["bab"] = ("active", "")
    kubectl = FakeKubectl(ready={"bab": (2, 2)})
    github = FakeGitHub(runs={"o/bab": ("completed", "failure")})
    _, report, _ = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert "dispatch:o/bab:gojira-ci.yaml" in github.calls
    assert "bab: deploy dispatched (the last deploy had failed)" in report.done
    # pods present and the last deploy fine: nothing to run
    github = FakeGitHub(runs={"o/bab": ("completed", "success")})
    gojira = FakeGojira()
    gojira.connect_outcome["bab"] = ("active", "")
    _, report, _ = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert not any(c.startswith("dispatch") for c in github.calls)


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


def test_an_expired_bootstrap_code_gets_the_pod_restarted_once(home):
    """A cabal pod up for longer than five minutes holds a dead code (a rerun,
    or a slow build): the installer tries it, is refused, restarts the
    deployment and logs in with the fresh one."""
    stack = _stack(home, [
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal"},
        {"name": "liminal", "repo": "o/liminal",
         "secrets": {"liminal-secrets": {"T": "cabal-token:service:liminal:secrets-fetch"}}},
    ])
    kubectl = FakeKubectl(log_code="OLD123")
    cabal = FakeCabal(expired={"OLD123"})
    _, report, lines = _run(home, stack, kubectl=kubectl, cabal=cabal, merge_setup_prs=True)
    assert "restart:cabal/cabal" in kubectl.calls
    assert cabal.logged_in == "FRESH1"
    assert "cabal: restarted, operator logged in with the fresh code" in report.done
    assert any("refused" in ln for ln in lines)


def test_no_code_in_the_log_also_restarts(home):
    stack = _stack(home, [
        {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal"},
        {"name": "liminal", "repo": "o/liminal",
         "secrets": {"liminal-secrets": {"T": "cabal-token:service:liminal:secrets-fetch"}}},
    ])
    kubectl = FakeKubectl(log_code=None)
    cabal = FakeCabal()
    _run(home, stack, kubectl=kubectl, cabal=cabal, merge_setup_prs=True)
    assert "restart:cabal/cabal" in kubectl.calls and cabal.logged_in == "FRESH1"


# --- gojira#108: a published image deployed from a repo of the operator's own


def _upstream(home):
    """The clone of an upstream under --repos: its contract (with build args and
    an arch the install should not inherit) and the k8s/base it keeps."""
    up = home / "repos" / "cabal"
    (up / "k8s" / "base").mkdir(parents=True)
    (up / "k8s" / "cluster").mkdir()
    (up / "gojira.yaml").write_text(
        "app: cabal\nclass: gateway\nenvironments:\n  - {name: dev, cluster: other, namespace: cabal}\n"
        "secrets_from: [cabal-secrets]\nenv:\n  VERTEX_PROJECT: upstream-proj\n  LOG: info\n"
        "identity: {gsa: cabal@upstream-proj.iam.gserviceaccount.com, gcp_roles: [roles/x]}\n"
        "build_args: {EXTRA_INDEX_URL: x}\narch: arm64\nnode_selector: {a: b}\nport: 7400\n"
    )
    (up / "k8s" / "base" / "pvc.yaml").write_text("kind: PersistentVolumeClaim\n")
    (up / "k8s" / "base" / "deployment.yaml").write_text("kind: Deployment\n")
    (up / "k8s" / "cluster" / "rbac.yaml").write_text("kind: ClusterRole\n")


def test_a_published_image_gets_a_deploy_repo_the_installer_writes_and_connects(home):
    _upstream(home)
    stack = _stack(home, [{
        "name": "cabal", "repo": "tenishai/cabal", "image": "r.example/tenish/cabal:3.2",
        "deploy_repo": "acme/cabal-deploy", "cluster_objects": ["k8s/cluster/rbac.yaml"],
        "contract": {"env": {"VERTEX_PROJECT": "proj"}, "identity": {"gcp_roles": ["roles/aiplatform.user"]}},
    }])
    gojira, kubectl, github = FakeGojira(), FakeKubectl(), FakeGitHub()
    _run(home, stack, gojira=gojira, kubectl=kubectl, github=github, merge_setup_prs=True)
    assert github.calls[:2] == ["create:acme/cabal-deploy", "push:acme/cabal-deploy:3"]
    files = github.repos["acme/cabal-deploy"]
    assert sorted(files) == ["gojira.yaml", "k8s/base/deployment.yaml", "k8s/base/pvc.yaml"]
    doc = yaml.safe_load(files["gojira.yaml"])
    assert doc["app"] == "cabal" and doc["image"] == "r.example/tenish/cabal:3.2"
    assert doc["environments"] == [{"name": "dev", "cluster": "gojira-sandbox", "namespace": "cabal"}]
    assert doc["secrets_from"] == ["cabal-secrets"] and doc["port"] == 7400          # the upstream's needs kept
    assert doc["env"] == {"VERTEX_PROJECT": "proj", "LOG": "info"}                  # contract: merged over
    assert doc["identity"] == {"gcp_roles": ["roles/aiplatform.user"]}     # upstream's gsa dropped, roles replaced
    for gone in ("build_args", "arch", "node_selector"):
        assert gone not in doc                                                      # nothing is built here
    assert [c for c in gojira.calls if c.startswith("connect")] == ["connect:cabal:auto=True"]
    assert gojira.connected["cabal"] == "acme/cabal-deploy"                        # the deploy repo, not upstream
    assert kubectl.applied == [str(home / "repos" / "cabal" / "k8s" / "cluster" / "rbac.yaml")]   # from upstream
    assert "merge:https://github.com/acme/cabal-deploy/pull/1" in github.calls           # its setup PR, not upstream's


def test_the_upstreams_adopted_account_never_travels(home):
    """identity.gsa names an account in the upstream's project; the install
    keeps the roles and leaves the account behind (litellm, 2026-10-22)."""
    _upstream(home)
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d"}])
    github = FakeGitHub()
    _run(home, stack, github=github, merge_setup_prs=True)
    doc = yaml.safe_load(github.repos["acme/d"]["gojira.yaml"])
    assert doc["identity"] == {"gcp_roles": ["roles/x"]}


def test_a_deploy_repo_rerun_pushes_nothing(home):
    _upstream(home)
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r.example/tenish/cabal:3.2",
                           "deploy_repo": "acme/cabal-deploy"}])
    github = FakeGitHub()
    _run(home, stack, github=github, merge_setup_prs=True)
    github.calls.clear()
    _, _, lines = _run(home, stack, github=github, merge_setup_prs=True)
    assert not [c for c in github.calls if c.startswith(("create", "push"))]
    assert "cabal: deploy repo acme/cabal-deploy current" in "\n".join(lines)


def test_a_changed_contract_on_a_live_app_is_provisioned_again(home):
    """A rerun that changes the deploy repo (bab's BAB_PORT, 2026-11-06) must
    press provision: the push's CI fails the contract check by design, and an
    active app would otherwise never pick the change up."""
    _upstream(home)
    entry = {"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d"}
    gojira, github = FakeGojira(), FakeGitHub()
    _run(home, _stack(home, [entry]), gojira=gojira, github=github, merge_setup_prs=True)
    assert gojira.app(1).status == "active"

    gojira.calls.clear()
    _run(home, _stack(home, [entry]), gojira=gojira, github=github, merge_setup_prs=True)
    assert not [c for c in gojira.calls if c.startswith("provision")]       # unchanged: left alone

    entry["contract"] = {"env": {"PORT": "8080"}}
    _, _, lines = _run(home, _stack(home, [entry]), gojira=gojira, github=github, merge_setup_prs=True)
    assert [c for c in gojira.calls if c.startswith("provision")] == ["provision:1"]
    assert "cabal: provision pressed again (deploy repo changed)" in "\n".join(lines)


def test_a_deploy_repo_without_the_upstream_clone_reads_github(home):
    """A stranger clones nothing: with no clone under --repos the upstream's
    k8s/base, gojira.yaml and cluster objects are read from GitHub as they are
    on its default branch. At 8e8b171 this stopped with "has no k8s/base"."""
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d",
                           "cluster_objects": ["k8s/cluster/rbac.yaml"]}])
    github = FakeGitHub(repos={"tenishai/cabal": {
        "gojira.yaml": "app: cabal\nclass: gateway\nsecrets_from: [cabal-secrets]\nport: 7400\narch: arm64\n",
        "k8s/base/deployment.yaml": "kind: Deployment\n", "k8s/base/kustomization.yaml": "resources: [deployment.yaml]\n",
        "k8s/base/README.md": "not yaml, still the upstream's\n", "k8s/cluster/rbac.yaml": "kind: ClusterRole\n",
        "k8s/overlays/dev/x.yaml": "never: copied\n"}})
    kubectl = FakeKubectl()
    _, _, lines = _run(home, stack, github=github, kubectl=kubectl, merge_setup_prs=True)
    files = github.repos["acme/d"]
    assert sorted(files) == ["gojira.yaml", "k8s/base/README.md", "k8s/base/deployment.yaml", "k8s/base/kustomization.yaml"]
    doc = yaml.safe_load(files["gojira.yaml"])
    assert doc["port"] == 7400 and doc["secrets_from"] == ["cabal-secrets"] and "arch" not in doc
    assert len(kubectl.applied) == 1 and kubectl.applied[0].endswith(".yaml")      # a temp file, then gone
    assert not Path(kubectl.applied[0]).exists()
    assert "cabal: k8s/base read from tenishai/cabal on GitHub" in "\n".join(lines)


def test_a_deploy_repo_with_neither_clone_nor_github_base_stops(home):
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d"}])
    with pytest.raises(ToolError, match="has no k8s/base"):
        _run(home, stack, github=FakeGitHub(), merge_setup_prs=True)


def test_the_installs_own_base_files_travel_and_are_listed(home):
    """An ExternalSecret only this install knows (its vault path) goes into the
    deploy repo's k8s/base and into the kustomization, or Gojira drops it from
    the build on the next provision (kobo-okta, 2026-11-05)."""
    _upstream(home)
    (home / "repos" / "cabal" / "k8s" / "base" / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
        "resources:\n  - deployment.yaml\n  - pvc.yaml\n")
    (home / "prod").mkdir()
    (home / "prod" / "externalsecret-okta.yaml").write_text("kind: ExternalSecret\n")
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d",
                           "base_files": ["prod/externalsecret-okta.yaml"]}])
    github = FakeGitHub()
    _run(home, stack, github=github, merge_setup_prs=True)
    files = github.repos["acme/d"]
    assert files["k8s/base/externalsecret-okta.yaml"] == "kind: ExternalSecret\n"
    kust = yaml.safe_load(files["k8s/base/kustomization.yaml"])
    assert kust["resources"] == ["deployment.yaml", "pvc.yaml", "externalsecret-okta.yaml"]
    github.calls.clear()
    _run(home, stack, github=github, merge_setup_prs=True)
    assert not [c for c in github.calls if c.startswith("push")]                      # a rerun is quiet


def test_base_files_without_an_upstream_kustomization_get_one(home):
    _upstream(home)
    (home / "es.yaml").write_text("kind: ExternalSecret\n")
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d",
                           "base_files": ["es.yaml"]}])
    github = FakeGitHub()
    _run(home, stack, github=github, merge_setup_prs=True)
    kust = yaml.safe_load(github.repos["acme/d"]["k8s/base/kustomization.yaml"])
    assert kust["resources"] == ["deployment.yaml", "pvc.yaml", "es.yaml"]


def test_a_base_file_may_not_replace_the_upstreams_own(home):
    _upstream(home)
    (home / "pvc.yaml").write_text("kind: PersistentVolumeClaim\n")
    stack = _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "image": "r/x:1", "deploy_repo": "acme/d",
                           "base_files": ["pvc.yaml"]}])
    with pytest.raises(ToolError, match="would replace"):
        _run(home, stack, github=FakeGitHub(), merge_setup_prs=True)


def test_base_files_need_a_deploy_repo(home):
    with pytest.raises(Exception, match="base_files"):
        _stack(home, [{"name": "cabal", "repo": "tenishai/cabal", "base_files": ["es.yaml"]}])


def test_install_can_name_its_apps(home):
    """Adding one app to a live install touches only that app: the others'
    repos are not connected, provisioned or rewritten."""
    stack = _stack(home, [{"name": "litellm", "repo": "o/litellm"}, {"name": "kobo", "repo": "o/kobo"}])
    gojira, github = FakeGojira(), FakeGitHub()
    clients = Clients(gojira=gojira, kubectl=FakeKubectl(), github=github)
    o = Options(stack_dir=home, repo_root=home / "repos", poll_seconds=0, pr_wait_seconds=0.5,
                rollout_seconds=0.5, say=lambda _: None, merge_setup_prs=True)
    Installer(stack, clients, o).install(["kobo"])
    assert list(gojira.connected) == ["kobo"]


def test_install_refuses_an_app_the_file_does_not_name(home):
    stack = _stack(home, [{"name": "kobo", "repo": "o/kobo"}])
    clients = Clients(gojira=FakeGojira(), kubectl=FakeKubectl(), github=FakeGitHub())
    o = Options(stack_dir=home, repo_root=home / "repos", say=lambda _: None)
    with pytest.raises(ToolError, match="not in the stack file: bab"):
        Installer(stack, clients, o).install(["bab"])


# --- criterion 5: a missing file pauses the run, and the run continues when it appears ---

def test_a_missing_source_file_pauses_and_continues_when_it_appears(home):
    """The stack file names a file nobody has put there yet. The run says which
    file and for what, tells on_pause, and continues when the file appears
    (here: a thread writes it and sets the pause's event). The Secret then holds
    the file's bytes, and the lines never do. At fa944a6 this was a stop
    (FileNotFoundError out of read_text, shown as crashed)."""
    import threading
    stack = _stack(home, [{"name": "kobo", "repo": "o/kobo",
                           "secrets": {"kobo-github": {"private-key.pem": "file:late.pem"}}}])
    gojira = FakeGojira()
    gojira.connect_outcome["kobo"] = ("active", "")
    kubectl = FakeKubectl(ready={"kobo": (1, 1)})
    github = FakeGitHub(runs={"o/kobo": ("completed", "success")})
    paused = []

    def on_pause(path, why):
        paused.append((path, why))
        ev = threading.Event()

        def appear():
            time.sleep(0.05)
            tmp = path.with_suffix(".tmp")
            tmp.write_text("KEY-BYTES-77")
            tmp.rename(path)          # whole, the way a person's mv lands it
            ev.set()
        threading.Thread(target=appear).start()
        return ev

    _, report, lines = _run(home, stack, gojira=gojira, kubectl=kubectl, github=github,
                            on_pause=on_pause, file_wait_seconds=5)
    assert paused == [(home / "late.pem", "kobo: secret kobo-github/private-key.pem")]
    assert kubectl.secrets[("kobo", "kobo-github")]["private-key.pem"] == "KEY-BYTES-77"
    text = "\n".join(lines)
    assert "kobo: secret kobo-github/private-key.pem needs " in text and "put it there and the run continues" in text
    assert "is there now" in text and "KEY-BYTES-77" not in text


def test_a_file_that_never_appears_stops_naming_it(home):
    stack = _stack(home, [{"name": "kobo", "repo": "o/kobo",
                           "secrets": {"kobo-github": {"private-key.pem": "file:never.pem"}}}])
    gojira = FakeGojira()
    gojira.connect_outcome["kobo"] = ("active", "")
    with pytest.raises(ToolError, match=r"never\.pem still missing after 0s"):
        _run(home, stack, gojira=gojira, kubectl=FakeKubectl(ready={"kobo": (1, 1)}),
             github=FakeGitHub(runs={"o/kobo": ("completed", "success")}), file_wait_seconds=0.2)


def test_a_template_takes_the_four_and_the_app_from_the_env_file(home):
    """A `template:` file carries {org} {project} {region} {cluster} beside
    {gojira_token}; {app_id} and {installation_id} are read only when named: the
    id from the run's env file, the installation from GitHub with the App's own
    key, on the profile's org. Fails at ae032c8 (only {gojira_token} was filled)."""
    (home / "kobo.tmpl").write_text(
        'github_org: "{org}"\norg_repo: "{org}/kobo-config"\ngcp_project: "{project}"\n'
        'gojira_token: "{gojira_token}"\ngithub_app_id: "{app_id}"\ngithub_app_installation_id: "{installation_id}"\n')
    env_file = home / "gojira.env"
    env_file.write_text("PROJECT=my-proj\nGOJIRA_GITHUB_APP_ID=5141957\nGOJIRA_GITHUB_APP_PEM=/keys/app.pem\n")
    stack = _stack(home, [{"name": "kobo", "repo": "o/kobo",
                           "secrets": {"kobo-config": {"config.yaml": f"template:{home}/kobo.tmpl"}}}])
    kubectl, github = FakeKubectl(), FakeGitHub()
    github.installations = {"myorg": "77"}
    _run(home, stack, kubectl=kubectl, github=github, merge_setup_prs=True,
         values={"org": "myorg", "project": "my-proj", "region": "us-central1", "cluster": "gojira-sandbox"},
         env_file=env_file)
    doc = yaml.safe_load(kubectl.secrets[("kobo", "kobo-config")]["config.yaml"])
    assert doc == {"github_org": "myorg", "org_repo": "myorg/kobo-config", "gcp_project": "my-proj",
                   "gojira_token": "tok-123", "github_app_id": "5141957", "github_app_installation_id": "77"}
    assert "installation_id:5141957:myorg" in github.calls


def test_a_template_naming_the_app_without_an_env_file_stops_with_the_reason(home):
    (home / "kobo.tmpl").write_text('github_app_id: "{app_id}"\n')
    stack = _stack(home, [{"name": "kobo", "repo": "o/kobo",
                           "secrets": {"kobo-config": {"config.yaml": f"template:{home}/kobo.tmpl"}}}])
    with pytest.raises(ToolError, match="no env file"):
        _run(home, stack, merge_setup_prs=True)
    # and a token the profile never set is named, not written as "{org}"
    (home / "kobo.tmpl").write_text('github_org: "{org}"\n')
    with pytest.raises(ToolError, match=r"needs values for \{org\}"):
        _run(home, stack, merge_setup_prs=True)
