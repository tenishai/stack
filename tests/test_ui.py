"""`stack ui`: the same engine behind buttons, its lines on the page as they happen."""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stack.clients import ToolError
from stack.preflight import Check
from stack.runs import Runner
from stack.steps import Report
from stack.ui import make_app

ROOT = Path(__file__).parent.parent
DOGFOOD = ROOT / "examples" / "dogfood.yaml"


def _wait(run, seconds=10):
    deadline = time.monotonic() + seconds
    while run.finished is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert run.finished is not None, "the run never finished"


@pytest.fixture
def site(tmp_path):
    calls = []

    def perform(target, action, apps, *, merge_setup_prs=False, yes=False, merge_pins=False, say=print):
        calls.append((target, action, apps, merge_setup_prs, yes, merge_pins))
        say("== litellm")
        say("  litellm: connected (app 1)")
        if "boom" in apps:
            raise ToolError("litellm: provision failed; see the app's page on Gojira")
        return Report(done=["litellm: connected"])

    def preflight(stack, target):
        return [Check("gh", "ok", "logged in as tester"),
                Check("gh: acme", "fail", "your gh token has no SSO grant for acme")]

    runner = Runner()
    app = make_app(tmp_path / "profiles.yaml", runner, perform=perform, preflight=preflight)
    client = TestClient(app)
    client.calls, client.runner, client.profiles_path = calls, runner, tmp_path / "profiles.yaml"
    return client


def _add(site, **fields):
    form = {"name": "dogfood", "stack_file": str(DOGFOOD), "kubeconfig": "~/.kube-dogfood/config",
            "cloud_account": "me@example.com", "cbl": "~/cabal/.venv/bin/cbl", **fields}
    return site.post("/profiles", data=form, follow_redirects=False)


def test_a_profile_shows_its_stack_file_and_stores_only_paths(site):
    assert "No profiles yet" in site.get("/").text
    r = _add(site)
    assert r.status_code == 303 and r.headers["location"] == "/p/dogfood"
    page = site.get("/p/dogfood").text
    for word in ("litellm", "cabal", "liminal", "kobo", "gke_shaz-learning_us-central1_gojira-sandbox",
                 "tenishai/kobo", "upgrade gate: pr"):
        assert word in page
    saved = site.profiles_path.read_text()
    assert "kubeconfig: ~/.kube-dogfood/config" in saved and "me@example.com" in saved
    assert "dogfood" in site.get("/").text
    # a bad name is refused on the page, not with a traceback
    assert site.post("/profiles", data={"name": "my prod", "stack_file": "x.yaml"}).status_code == 422
    # a stack file that does not parse is one sentence on the profile page
    _add(site, name="broken", stack_file=str(site.profiles_path.parent / "nope.yaml"))
    assert "nope.yaml" in site.get("/p/broken").text


def test_a_run_gets_the_profiles_target_and_shows_its_lines_and_its_stop(site):
    _add(site)
    r = site.post("/p/dogfood/run", data={"action": "install", "apps": ["litellm", "boom"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/runs/1"
    run = site.runner.runs[1]
    _wait(run)
    target, action, apps, merge, yes, pins = site.calls[0]
    assert action == "install" and apps == ["litellm", "boom"] and yes is False
    assert merge is True and pins is True            # the press is the review
    assert target.stack_file == DOGFOOD and target.cbl.endswith("cabal/.venv/bin/cbl")
    assert target.env["CLOUDSDK_CORE_ACCOUNT"] == "me@example.com"
    assert target.env["KUBECONFIG"].endswith(".kube-dogfood/config") and "~" not in target.env["KUBECONFIG"]
    page = site.get("/runs/1").text
    assert "b-failed" in page
    assert '<div class="line head"><span class="text">litellm</span>' in page
    assert "litellm: connected (app 1)" in page
    assert 'class="line fail"' in page and "stopped: litellm: provision failed" in page
    assert "hx-trigger" not in page                      # finished: the page stops polling
    # a run that ends well says so; a bulk upgrade names its apps
    assert site.post("/p/dogfood/run", data={"action": "upgrade"}).status_code == 422
    site.post("/p/dogfood/run", data={"action": "upgrade", "apps": ["kobo"], "yes": "true"}, follow_redirects=False)
    _wait(site.runner.runs[2])
    assert site.calls[1][1:] == ("upgrade", ["kobo"], True, True, True)
    lines = site.get("/runs/2/lines").text
    assert "done: 1 steps" in lines
    assert 'id="going" hx-swap-oob="true"></span>' in lines      # the header badge clears with the run
    assert site.get("/runs/2").text.count('id="going"') == 1       # the full page carries it once
    assert "#2" in site.get("/p/dogfood").text and "upgrade · kobo" in site.get("/p/dogfood").text


def test_check_renders_the_preflight_rows(site):
    _add(site)
    r = site.post("/p/dogfood/check", follow_redirects=False)
    assert r.status_code == 303
    _wait(site.runner.runs[1])
    page = site.get("/runs/1").text
    assert 'class="line ok"' in page and "logged in as tester" in page
    assert 'class="line fail"' in page and "no SSO grant for acme" in page
    assert "1 missing, 0 to look at" in site.get("/p/dogfood").text


def test_one_run_at_a_time(tmp_path):
    gate = threading.Event()

    def perform(target, action, apps, **kw):
        gate.wait(10)
        return Report()

    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: []))
    _add(site)
    assert site.post("/p/dogfood/run", data={"action": "install"}, follow_redirects=False).status_code == 303
    second = site.post("/p/dogfood/run", data={"action": "upgrade", "apps": ["kobo"]}, follow_redirects=False)
    assert second.status_code == 409 and "/runs/1" in second.text
    assert site.post("/p/dogfood/check", follow_redirects=False).status_code == 409
    assert "run #1" in site.get("/p/dogfood").text and "Install</button>" not in site.get("/p/dogfood").text
    assert 'hx-get="/runs/1/lines"' in site.get("/runs/1").text   # still polling
    gate.set()
    _wait(runner.runs[1])
    assert runner.runs[1].status == "done" and runner.active() is None
    assert site.post("/p/dogfood/run", data={"action": "install"}, follow_redirects=False).status_code == 303
    assert site.get("/runs/99").status_code == 404


def test_a_crash_in_the_engine_is_a_line_not_a_hung_page(tmp_path):
    def perform(*a, **kw):
        raise KeyError("image")

    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: []))
    _add(site)
    site.post("/p/dogfood/run", data={"action": "install"}, follow_redirects=False)
    _wait(runner.runs[1])
    page = site.get("/runs/1").text
    assert "crashed: KeyError" in page and "image" in page and runner.active() is None


def test_pick_a_release_pins_it_in_the_stack_file_and_runs_that_app(tmp_path):
    from tests.fakes import FakeCrane

    stack_file = tmp_path / "s.yaml"
    stack_file.write_text(
        "cluster: {name: c, gcp_project: p, location: us-central1}\n"
        "apps:\n"
        "  - name: kobo\n"
        "    repo: o/kobo\n"
        "    image: r/agent-images/kobo@sha256:" + "a" * 64 + "   # v1, by hand\n"
        "    source_image: ghcr.io/o/kobo\n"
        "    deploy_repo: o/kobo-deploy\n"
        "  - {name: litellm, repo: o/litellm, image: r/agent-images/litellm:1, deploy_repo: o/litellm-deploy}\n"
    )
    crane = FakeCrane(digests={"ghcr.io/o/kobo:v2.0": "sha256:" + "b" * 64})
    crane.tags_by["ghcr.io/o/kobo"] = ["latest", "v1.0", "v2.0", "sha-abc", "v1.9"]
    calls = []

    def perform(target, action, apps, **kw):
        calls.append((action, apps, kw["yes"], kw["merge_setup_prs"], kw["merge_pins"]))
        return Report()

    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: [],
                               crane=lambda env: crane))
    site.post("/profiles", data={"name": "t", "stack_file": str(stack_file)})
    page = site.get("/p/t").text
    assert 'hx-get="/p/t/tags/kobo"' in page and "set <span class=\"mono\">source_image:</span>" in page
    frag = site.get("/p/t/tags/kobo").text
    assert frag.index('value="v2.0"') < frag.index('value="v1.9"') < frag.index('value="v1.0"')
    assert "latest" not in frag and "sha-abc" not in frag and "now pinned by digest only" in frag
    assert site.get("/p/t/tags/litellm").status_code == 200 and "no source_image" in site.get("/p/t/tags/litellm").text
    # an unpublished tag is refused before the file changes
    r = site.post("/p/t/upgrade-to", data={"app": "kobo", "tag": "v9"})
    assert r.status_code == 422 and "manifest unknown" in r.text and "a" * 64 in stack_file.read_text()
    r = site.post("/p/t/upgrade-to", data={"app": "kobo", "tag": "v2.0", "yes": "true"}, follow_redirects=False)
    assert r.status_code == 303
    _wait(runner.runs[1])
    text = stack_file.read_text()
    assert "    image: r/agent-images/kobo:v2.0@sha256:" + "b" * 64 + "   # v2.0, pinned by stack ui\n" in text
    assert "image: r/agent-images/litellm:1" in text and "by hand" not in text
    assert calls == [("upgrade", ["kobo"], True, True, True)]
    run_page = site.get("/runs/1").text
    assert "stack file now pins r/agent-images/kobo:v2.0@sha256:bbbb" in run_page and "upgrade · kobo to v2.0" in site.get("/p/t").text
