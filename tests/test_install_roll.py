"""Install's done is the rolled deployment (criterion 6).

After the setup PR merges and the deploy is dispatched, an image app's install
is done only when the pods run the digest the stack file pins — the 2026-11-10
kobo memory change said "done" while the old pods were still up. A post-merge
run that fails is a stop with the Actions URL, as upgrade's wait_rolled is."""

from pathlib import Path

from stack.clients import ToolError
from stack.model import Stack
from stack.steps import Clients, Installer, Options
from tests.fakes import FakeGitHub, FakeGojira, FakeKubectl

ROOT = Path(__file__).parent.parent

OLD_DIGEST = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW_DIGEST = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _stack(tmp_path, app):
    return Stack.model_validate({
        "cluster": {"name": "gojira-sandbox", "gcp_project": "proj", "location": "us-central1"},
        "apps": [app],
    })


def _install(tmp_path, stack, *, gojira, kubectl, github, rollout_seconds=5.0, auto_merge=True, **opts):
    clients = Clients(gojira=gojira, kubectl=kubectl, github=github)
    lines = []
    o = Options(stack_dir=tmp_path, repo_root=tmp_path / "repos", poll_seconds=0,
                pr_wait_seconds=0.5, rollout_seconds=rollout_seconds, bootstrap_seconds=0.5,
                merge_setup_prs=auto_merge, say=lines.append, **opts)
    inst = Installer(stack, clients, o)
    try:
        return inst, inst.install(), lines
    except ToolError as e:
        return inst, e, lines


def _upstream(tmp_path, repo, gojira_yaml):
    base = tmp_path / "repos" / repo.split("/")[1] / "k8s" / "base"
    base.mkdir(parents=True, exist_ok=True)
    (base / "deployment.yaml").write_text("kind: Deployment\n")
    (tmp_path / "repos" / repo.split("/")[1] / "gojira.yaml").write_text(gojira_yaml)


def _app(image, deploy_repo, repo="up/kobo"):
    return {"name": "kobo", "repo": repo, "deploy_repo": deploy_repo, "image": image}


def test_install_is_done_when_the_pods_run_the_pinned_digest(tmp_path):
    """The green line: the deployment runs the digest the stack file pins
    and the pods are ready — install is done, and the note says the digest."""
    stack = _stack(tmp_path, _app(f"reg.example/apps/kobo@{NEW_DIGEST}", "org/kobo-deploy"))
    _upstream(tmp_path, "up/kobo", f"image: reg.example/apps/kobo@{NEW_DIGEST}\n")
    gojira = FakeGojira()
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    github = FakeGitHub()
    inst, result, lines = _install(tmp_path, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert not isinstance(result, ToolError), f"install failed: {result}"
    assert any("rolled" in line and NEW_DIGEST[:19] in line for line in lines)
    assert report_waits_empty(inst)


def report_waits_empty(inst):
    """The rollout wait is a receipt, not an open waiter the page must chase."""
    return inst.report.waited_on == []


def test_install_waits_until_the_roll_lands(tmp_path):
    """The kobo memory change: said done while the old pods were still up.
    Install must not finish before the deployment runs the new digest — it
    ends the rollout window with the Actions URL while the old digest is
    still running; the happy path (test 1) is the roll landing in time."""
    stack = _stack(tmp_path, _app(f"reg.example/apps/kobo@{NEW_DIGEST}", "org/kobo-deploy"))
    _upstream(tmp_path, "up/kobo", f"image: reg.example/apps/kobo@{NEW_DIGEST}\n")
    gojira = FakeGojira()
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{OLD_DIGEST}"})  # the old pod never flips
    github = FakeGitHub()
    inst, result, lines = _install(tmp_path, stack, gojira=gojira, kubectl=kubectl, github=github,
                                   rollout_seconds=1.0)
    assert isinstance(result, ToolError)
    assert "pods not on" in str(result) and NEW_DIGEST[:19] in str(result)
    assert "https://github.com/org/kobo-deploy/actions" in str(result)
    # The happy path is unchanged: nothing said about a run — the pods are the receipt.
    assert not any("run failed" in line for line in lines)


def test_a_failed_post_merge_run_stops_with_the_actions_url(tmp_path):
    """A post-merge run that completes without success is a stop with the
    Actions URL, in time to say which run. (The upgrade's own pin-push
    contract-check failure completes before the provision, so it is not a
    change the roll watch sees — no false stop there.)"""
    stack = _stack(tmp_path, _app(f"reg.example/apps/kobo@{NEW_DIGEST}", "org/kobo-deploy"))
    _upstream(tmp_path, "up/kobo", f"image: reg.example/apps/kobo@{NEW_DIGEST}\n")
    gojira = FakeGojira()
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{OLD_DIGEST}"})
    github = FakeGitHub()
    # The run the merged setup PR starts fails, and the pods never flip. The
    # run reads in-flight at the mark (a merge that has not settled yet), then
    # completes as a failure on the next poll — so the stop arrives inside the
    # window, while the window is what an operator would still be watching.
    kubectl.image_script["kobo"] = [f"reg.example/apps/kobo@{OLD_DIGEST}"] * 3
    kubectl.image_hooks["kobo"] = [lambda i: github.complete("org/kobo-deploy", "failure") if i >= 1 else None]
    github.leave_pending("org/kobo-deploy")
    inst, result, lines = _install(tmp_path, stack, gojira=gojira, kubectl=kubectl, github=github,
                                   rollout_seconds=2.0)
    assert isinstance(result, ToolError)
    assert "deploy run failed" in str(result)
    assert "https://github.com/org/kobo-deploy/actions" in str(result)


def _direct_install(tmp_path, stack, *, gojira, kubectl, github, rollout_seconds=5.0, auto_merge=True, **opts):
    """Build the installer and run the roll watch directly, per Ember's probe
    script: the install's other steps are not the thing under test here."""
    clients = Clients(gojira=gojira, kubectl=kubectl, github=github)
    lines = []
    o = Options(stack_dir=tmp_path, repo_root=tmp_path / "repos", poll_seconds=0,
                pr_wait_seconds=0.5, rollout_seconds=rollout_seconds, bootstrap_seconds=0.5,
                merge_setup_prs=auto_merge, say=lines.append, **opts)
    inst = Installer(stack, clients, o)
    try:
        inst.wait_rolled(stack.apps[0], NEW_DIGEST, mark=True)
        return inst, None, lines
    except ToolError as e:
        return inst, e, lines


def test_probe_a_queued_at_the_mark_is_waited_not_stopped(tmp_path):
    """Probe A (Ember): the run reads queued at the mark, in_progress on the
    next poll, and the digest flips on the third. `queued`/`in_progress` are
    waiting states, whatever they were at the mark — the watch waits and ends
    "rolled"; it does not stop."""
    stack = _stack(tmp_path, _app(f"reg.example/apps/kobo@{NEW_DIGEST}", "org/kobo-deploy"))
    gojira, kubectl, github = FakeGojira(), FakeKubectl(), FakeGitHub()
    kubectl.image_script["kobo"] = [f"reg.example/apps/kobo@{OLD_DIGEST}",
                                    f"reg.example/apps/kobo@{OLD_DIGEST}",
                                    f"reg.example/apps/kobo@{NEW_DIGEST}"]   # flip on poll 3
    github.runs["org/kobo-deploy"] = ("queued", "")                          # queued at the mark
    # the run goes in flight on the first image poll, before the flip (i=0); by
    # the third poll the digest has landed and the watch returns before re-reading
    kubectl.image_hooks["kobo"] = [lambda i: (github.runs.__setitem__("org/kobo-deploy", ("in_progress", ""))) if i == 0 else None]
    inst, result, lines = _direct_install(tmp_path, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert result is None, f"probe A stopped the install: {result}"
    assert any("rolled" in line for line in lines)


def test_probe_b_earlier_run_at_the_mark_is_not_the_merge_run(tmp_path):
    """Probe B (Ember, the 2026-11-10 kobo install): an earlier run completed
    success at the mark; the merge's run appears queued on the next poll and the
    digest flips later. The mark records state at the mark; a run in flight that
    arrives after it is the thing to wait for, never a stop — "rolled"."""
    stack = _stack(tmp_path, _app(f"reg.example/apps/kobo@{NEW_DIGEST}", "org/kobo-deploy"))
    gojira, kubectl, github = FakeGojira(), FakeKubectl(), FakeGitHub()
    kubectl.image_script["kobo"] = [f"reg.example/apps/kobo@{OLD_DIGEST}",
                                    f"reg.example/apps/kobo@{OLD_DIGEST}",
                                    f"reg.example/apps/kobo@{NEW_DIGEST}"]   # flip on poll 3
    github.runs["org/kobo-deploy"] = ("completed", "success")                # the earlier run, at the mark
    kubectl.image_hooks["kobo"] = [lambda i: github.runs.__setitem__("org/kobo-deploy", ("queued", "")) if i == 0 else None]
    inst, result, lines = _direct_install(tmp_path, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert result is None, f"probe B stopped the install: {result}"
    assert any("rolled" in line for line in lines)


def test_source_app_install_is_unchanged(tmp_path):
    """No deploy_repo: install ends where it always did (pods ready); no
    digest waiting is invented for an app the registry does not hold."""
    stack = _stack(tmp_path, {"name": "litellm", "repo": "up/litellm"})
    _upstream(tmp_path, "up/litellm", "image: reg.example/apps/litellm:1.0\n")
    gojira = FakeGojira()
    kubectl = FakeKubectl()
    github = FakeGitHub()
    inst, result, lines = _install(tmp_path, stack, gojira=gojira, kubectl=kubectl, github=github)
    assert not isinstance(result, ToolError), f"install failed: {result}"
    assert any("pods ready" in line for line in lines)
    assert not any("digest" in line or "/actions" in line for line in lines)
