"""Upgrade flow against fakes: the digest bump, the gate, and the roll."""

from pathlib import Path

import pytest

from stack.clients import AppRow, ToolError
from stack.model import Stack
from stack.steps import Clients, Installer, Options
from tests.fakes import FakeCrane, FakeGitHub, FakeGojira, FakeKubectl

ROOT = Path(__file__).parent.parent

OLD_DIGEST = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW_DIGEST = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MOVED_DIGEST = "sha256:ccccccccccccccccbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

GOJIRA_YAML = f"""image: reg.example/apps/kobo@{OLD_DIGEST}   # old
"""

GOJIRA_YAML_DATA = f"""image: reg.example/apps/kobo@{OLD_DIGEST}   # old
data_mount:
  pvc: kobo-data
"""


def _stack(tmp_path, apps, *, upgrade_gate="pr"):
    return Stack.model_validate({
        "cluster": {"name": "gojira-sandbox", "gcp_project": "proj", "location": "us-central1"},
        "apps": apps,
        "upgrade_gate": upgrade_gate,
    })


def _upgrade(tmp_path, stack, *, gojira=None, kubectl=None, github=None, crane=None, **opts):
    clients = Clients(
        gojira=gojira or FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")]),
        kubectl=kubectl or FakeKubectl(),
        github=github or FakeGitHub(),
        crane=crane,
    )
    lines = []
    o = Options(
        stack_dir=tmp_path,
        repo_root=tmp_path / "repos",
        poll_seconds=0,
        pr_wait_seconds=0.5,
        rollout_seconds=0.5,
        bootstrap_seconds=0.5,
        say=lines.append,
        **opts,
    )
    inst = Installer(stack, clients, o)
    return inst, inst.upgrade(["kobo"]), lines


def test_app_without_deploy_repo_is_skipped(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "image": "reg.example/apps/kobo:v2"}])
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    github = FakeGitHub()
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, crane=FakeCrane())
    assert any("upgrade skipped" in line for line in lines)
    assert gojira.calls == []
    assert github.calls == []


def test_already_at_the_digest_does_nothing(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": f"reg.example/apps/kobo@{NEW_DIGEST}"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": f"image: reg.example/apps/kobo@{NEW_DIGEST}   # old\n"}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    crane = FakeCrane(digests={f"reg.example/apps/kobo@{NEW_DIGEST}": NEW_DIGEST})
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, crane=crane)
    assert any("nothing to do" in line for line in lines)
    assert not any(c.startswith("push:") for c in github.calls)
    assert not any(c.startswith("open_pr:") for c in github.calls)
    assert not any(c.startswith("provision:") for c in gojira.calls)


def test_a_deploy_line_pinned_by_tag_at_the_same_digest_does_nothing(tmp_path):
    """The install writes the deploy repo's image: by tag. Upgrade compares
    what that tag resolves to; re-pinning the same bytes by digest is not an
    upgrade (an Upgrade pressed on a live install opened one, 2026-11-09)."""
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": "image: reg.example/apps/kobo:v2   # by tag\n"}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, crane=crane)
    assert any("nothing to do" in line for line in lines)
    assert not any(c.startswith("open_pr:") for c in github.calls) and gojira.calls == []


def test_missing_digest_is_copied_from_source_image(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2", "source_image": "ghcr.io/up/kobo"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={"ghcr.io/up/kobo:v2": NEW_DIGEST})
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)
    assert ("ghcr.io/up/kobo:v2", "reg.example/apps/kobo:v2") in crane.copied
    assert any("upgraded" in line for line in lines)


def test_pr_gate_opens_a_pr_and_provisions_after_merge(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}], upgrade_gate="pr")
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)
    assert any(c.startswith("open_pr:") for c in github.calls)
    assert "provision:1" in gojira.calls
    assert f"reg.example/apps/kobo@{NEW_DIGEST}" in github.repos["org/kobo-deploy"]["gojira.yaml"]
    assert any("upgraded" in line for line in lines)


def test_pr_gate_with_merge_pins_merges_its_own_pr(tmp_path):
    """The page's press is the review: the run merges the pin PR it opened
    and goes on, instead of waiting on a person (2026-11-09)."""
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": f"reg.example/apps/kobo@{NEW_DIGEST}"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    github.open_pr_state = "open"
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={f"reg.example/apps/kobo@{NEW_DIGEST}": NEW_DIGEST})
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl,
                                   crane=crane, merge_pins=True)
    assert any(c.startswith("open_pr:") for c in github.calls)
    assert any(c.startswith("merge:https://github.com/org/kobo-deploy/pull/") for c in github.calls), github.calls
    assert report.waited_on == [] and not any("waiting for someone" in line for line in lines)
    assert any("provision:1" == c for c in gojira.calls) and any("upgraded" in line for line in lines)


def test_push_gate_pushes_directly(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}], upgrade_gate="push")
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})
    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)
    assert any(c.startswith("push:") for c in github.calls)
    assert not any(c.startswith("open_pr:") for c in github.calls)


def test_data_mount_is_refused_without_yes(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML_DATA}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})

    with pytest.raises(ToolError, match="kobo-data"):
        _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)

    inst, report, lines = _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane, yes=True)
    assert any("upgraded" in line for line in lines)


def test_a_moved_tag_is_refused(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": f"reg.example/apps/kobo:v2@{MOVED_DIGEST}"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})

    with pytest.raises(ToolError, match="tag moved"):
        _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)


def test_unknown_app_name_is_refused(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}])
    clients = Clients(
        gojira=FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")]),
        kubectl=FakeKubectl(),
        github=FakeGitHub(),
        crane=FakeCrane(),
    )
    lines = []
    o = Options(stack_dir=tmp_path, repo_root=tmp_path / "repos", poll_seconds=0,
                pr_wait_seconds=0.5, rollout_seconds=0.5, bootstrap_seconds=0.5, say=lines.append)
    inst = Installer(stack, clients, o)
    with pytest.raises(ToolError, match="not in the stack file"):
        inst.upgrade(["nosuch"])


def test_a_closed_pin_pr_stops_the_upgrade(tmp_path):
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}], upgrade_gate="pr")
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    github.open_pr_state = "closed"
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{NEW_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})

    with pytest.raises(ToolError, match="closed"):
        _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)

def test_a_roll_that_never_lands_times_out(tmp_path):
    """The deployment never picks up the new digest: the upgrade stops with
    the Actions page, after the rollout window."""
    stack = _stack(tmp_path, [{"name": "kobo", "repo": "up/kobo", "deploy_repo": "org/kobo-deploy",
                               "image": "reg.example/apps/kobo:v2"}])
    github = FakeGitHub(repos={"org/kobo-deploy": {"gojira.yaml": GOJIRA_YAML}})
    gojira = FakeGojira(apps=[AppRow(id=1, name="kobo", status="active")])
    kubectl = FakeKubectl(images={"kobo": f"reg.example/apps/kobo@{OLD_DIGEST}"})
    crane = FakeCrane(digests={"reg.example/apps/kobo:v2": NEW_DIGEST})

    with pytest.raises(ToolError, match="pods not on"):
        _upgrade(tmp_path, stack, gojira=gojira, github=github, kubectl=kubectl, crane=crane)
