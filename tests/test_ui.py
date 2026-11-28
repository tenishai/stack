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

    def perform(target, action, apps, *, merge_setup_prs=False, yes=False, merge_pins=False, say=print, **_):
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
    assert "none yet" in site.get("/").text
    r = _add(site)
    assert r.status_code == 303 and r.headers["location"] == "/p/dogfood"
    page = site.get("/p/dogfood").text
    for word in ("litellm", "cabal", "liminal", "kobo", "gke_shaz-learning_us-central1_gojira-sandbox",
                 "tenishai/kobo", "Not checked since stack ui started"):
        assert word in page
    assert page.index("Run check") < page.index("Releases") < page.index("Install all 4</button>")   # the page's order
    assert 'class="env-dev"' in page and 'rail-idle' in page and "not checked" in page
    saved = site.profiles_path.read_text()
    assert "kubeconfig: ~/.kube-dogfood/config" in saved and "me@example.com" in saved
    assert 'class="env">dev</span>' in site.get("/").text   # the list wears each install's environment
    # a bad name is refused on the page, not with a traceback
    assert site.post("/profiles", data={"name": "my prod", "stack_file": "x.yaml"}).status_code == 422
    # a stack file that does not parse is one sentence on the profile page
    _add(site, name="broken", stack_file=str(site.profiles_path.parent / "nope.yaml"))
    assert "nope.yaml" in site.get("/p/broken").text


def test_a_run_gets_the_profiles_target_and_shows_its_lines_and_its_stop(site):
    _add(site)
    r = site.post("/p/dogfood/install", data={"apps": ["litellm", "boom"]}, follow_redirects=False)
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
    assert "b-failed" in page and "rail-fail" in page
    assert '<div class="line head"><span class="text">litellm</span>' in page
    assert "litellm: connected (app 1)" in page
    assert 'class="line fail"' in page and "stopped: litellm: provision failed" in page
    assert "hx-trigger" not in page                      # finished: the page stops polling
    # a run that ends well says so; there is no bulk upgrade (the table's picker is the one surface)
    assert site.post("/p/dogfood/run", data={"action": "upgrade"}).status_code == 404
    site.post("/p/dogfood/install", data={"apps": ["kobo"]}, follow_redirects=False)
    _wait(site.runner.runs[2])
    assert site.calls[1][1:] == ("install", ["kobo"], True, False, True)
    lines = site.get("/runs/2/lines").text
    assert "done: 1 steps" in lines
    assert 'id="going" hx-swap-oob="true"></span>' in lines      # the header badge clears with the run
    assert site.get("/runs/2").text.count('id="going"') == 1       # the full page carries it once
    assert 'class="env">dev</span>' in site.get("/runs/2").text and "rail-ok" in site.get("/runs/2").text
    assert "#2" in site.get("/p/dogfood").text and "install · kobo" in site.get("/p/dogfood").text


def test_check_renders_the_preflight_rows(site):
    _add(site)
    r = site.post("/p/dogfood/check", follow_redirects=False)
    assert r.status_code == 303
    _wait(site.runner.runs[1])
    page = site.get("/runs/1").text
    assert 'class="line ok"' in page and "logged in as tester" in page
    assert 'class="line fail"' in page and "no SSO grant for acme" in page
    profile_page = site.get("/p/dogfood").text
    assert "1 missing, 0 to look at" in profile_page and "rail-fail" in profile_page   # the stage wears the result
    assert '<div class="line head"><span class="text">gh</span>' in page   # rows grouped under their section


def test_one_run_at_a_time(tmp_path):
    gate = threading.Event()

    def perform(target, action, apps, **kw):
        gate.wait(10)
        return Report()

    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: []))
    _add(site)
    assert site.post("/p/dogfood/install", data={}, follow_redirects=False).status_code == 303
    second = site.post("/p/dogfood/install", data={"apps": ["kobo"]}, follow_redirects=False)
    assert second.status_code == 409 and "/runs/1" in second.text and "a run is already going" in second.text
    assert site.post("/p/dogfood/check", follow_redirects=False).status_code == 409
    assert "run #1" in site.get("/p/dogfood").text and 'class="caution">Install all' not in site.get("/p/dogfood").text
    assert 'hx-get="/runs/1/lines"' in site.get("/runs/1").text   # still polling
    gate.set()
    _wait(runner.runs[1])
    assert runner.runs[1].status == "done" and runner.active() is None
    assert site.post("/p/dogfood/install", data={}, follow_redirects=False).status_code == 303
    assert site.get("/runs/99").status_code == 404


def test_a_crash_in_the_engine_is_a_line_not_a_hung_page(tmp_path):
    def perform(*a, **kw):
        raise KeyError("image")

    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: []))
    _add(site)
    site.post("/p/dogfood/install", data={}, follow_redirects=False)
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
        "  - {name: litellm, repo: o/litellm, image: r/agent-images/litellm:1, deploy_repo: o/litellm-deploy,\n"
        "     source_image: ghcr.io/o/litellm}\n"
    )
    crane = FakeCrane(digests={"ghcr.io/o/kobo:v2.0": "sha256:" + "b" * 64})
    crane.tags_by["ghcr.io/o/kobo"] = ["latest", "v1.0", "v2.0", "sha-abc", "v1.9"]
    crane.tags_by["ghcr.io/o/litellm"] = ["1.0", "1.1"]
    calls = []

    def perform(target, action, apps, **kw):
        calls.append((action, apps, kw["yes"], kw["merge_setup_prs"], kw["merge_pins"]))
        return Report()

    from tests.fakes import FakeGitHub

    github = FakeGitHub(repos={"o/kobo-deploy": {"gojira.yaml": "image: x\ndata_mount:\n  pvc: kobo-data\n"},
                               "o/litellm-deploy": {"gojira.yaml": "image: x\n"}})
    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: [],
                               crane=lambda env: crane, github=lambda env: github))
    site.post("/profiles", data={"name": "t", "stack_file": str(stack_file)})
    page = site.get("/p/t").text
    assert 'hx-get="/p/t/tags/kobo"' in page and 'hx-get="/p/t/tags/litellm"' in page
    frag = site.get("/p/t/tags/kobo").text
    assert "kobo-data</span> is backed up" in frag                    # the gate names the PVC
    assert '<option value="" selected>pick a release</option>' in frag   # nothing preselected: the pick is the decision
    assert frag.index('value="v2.0"') < frag.index('value="v1.9"') < frag.index('value="v1.0"')
    assert 'value="latest"' not in frag and 'value="sha-abc"' not in frag and 'data-current=""' in frag
    assert '<button type="submit" class="quiet" disabled>Upgrade to</button>' in frag   # off until a pick (and the gate)
    lite = site.get("/p/t/tags/litellm").text
    assert 'value="1.1"' in lite and "backed up" not in lite           # no PVC: no gate to tick
    assert 'value="1"' not in lite and 'data-current="1"' in lite       # the running tag is not a choice
    before = stack_file.read_text()
    # every refusal comes before anything is written
    assert "pick a release" in site.post("/p/t/upgrade-to", data={"app": "kobo", "tag": ""}).text
    assert "already pinned" in site.post("/p/t/upgrade-to", data={"app": "litellm", "tag": "1"}).text
    r = site.post("/p/t/upgrade-to", data={"app": "kobo", "tag": "v2.0"})
    assert r.status_code == 422 and "kobo-data" in r.text and "backup box" in r.text
    assert stack_file.read_text() == before and runner.runs == {}
    # an unpublished tag is refused by the run, before the file changes
    site.post("/p/t/upgrade-to", data={"app": "kobo", "tag": "v9", "yes": "true"}, follow_redirects=False)
    _wait(runner.runs[1])
    assert runner.runs[1].status == "failed" and "manifest unknown" in site.get("/runs/1").text
    assert stack_file.read_text() == before and calls == []
    r = site.post("/p/t/upgrade-to", data={"app": "kobo", "tag": "v2.0", "yes": "true"}, follow_redirects=False)
    assert r.status_code == 303
    _wait(runner.runs[2])
    text = stack_file.read_text()
    assert "    image: r/agent-images/kobo:v2.0@sha256:" + "b" * 64 + "   # v2.0, pinned by stack ui\n" in text
    assert "image: r/agent-images/litellm:1" in text and "by hand" not in text
    assert calls == [("upgrade", ["kobo"], True, True, True)]
    run_page = site.get("/runs/2").text
    assert "stack file now pins r/agent-images/kobo:v2.0@sha256:bbbb" in run_page and "upgrade · kobo to v2.0" in site.get("/p/t").text


def test_a_missing_file_is_a_pause_card_and_done_resumes(tmp_path):
    """Criterion 5: the engine says a file is missing (on_pause); the run page shows
    a card with the path and what it is for, the status is paused, and the file's
    contents never appear. Done sets the event; the engine continues; the card is
    gone and the run is done. At fa944a6 perform had no on_pause and Run no pause."""
    body = "SECRET-FILE-BODY-9C1E"
    pem = tmp_path / "app.pem"
    got = {}

    def perform(target, action, apps, *, say=print, on_pause=None, on_resume=None, **_):
        say("== kobo")
        ev = on_pause(pem, "kobo: secret kobo-github/private-key.pem")
        got["event"] = ev
        ev.wait(5)                              # Done, or the test's deadline
        assert pem.read_text() == body          # read only after it is there
        on_resume()
        say("  kobo: secret kobo-github created (private-key.pem)")
        return Report(done=["kobo: secret"])

    runner = Runner()
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: []))
    site.post("/profiles", data={"name": "dogfood", "stack_file": str(DOGFOOD)})
    site.post("/p/dogfood/install", data={"apps": ["kobo"]}, follow_redirects=False)
    run = runner.runs[1]
    for _ in range(200):
        if run.paused is not None:
            break
        time.sleep(0.01)
    assert run.status == "paused"
    page = site.get("/runs/1").text
    assert "waiting for a file" in page and str(pem) in page and "kobo-github/private-key.pem" in page
    assert "b-paused" in page and 'action="/runs/1/resume"' in page
    assert "hx-trigger" in page                      # a paused run keeps polling
    pem.write_text(body)
    r = site.post("/runs/1/resume", follow_redirects=False)
    assert r.status_code == 303
    _wait(run)
    assert run.status == "done" and run.paused is None
    page = site.get("/runs/1").text
    assert "waiting for a file" not in page and body not in page
    assert "kobo: secret kobo-github created" in page


def test_runs_outlive_the_process_and_an_unfinished_one_says_so(tmp_path):
    """Criterion 7: a Runner with a store writes each run as it goes; a new Runner
    on the same store shows every run with its lines and how it ended, numbers
    the next run after them, and marks a run the process died under as failed
    with the restart line. At 34ad01b runs lived in memory only."""
    import json
    store = tmp_path / "runs"

    def perform(target, action, apps, *, say=print, **_):
        say("== litellm")
        say("  litellm: connected (app 1)")
        if "boom" in apps:
            raise ToolError("litellm: provision failed twice; see http://127.0.0.1:1/apps/1")
        return Report(done=["litellm: connected"])

    runner = Runner(store)
    site = TestClient(make_app(tmp_path / "p.yaml", runner, perform=perform, preflight=lambda s, t: []))
    _add(site)
    site.post("/p/dogfood/install", data={"apps": ["litellm"]}, follow_redirects=False)
    _wait(runner.runs[1])
    site.post("/p/dogfood/install", data={"apps": ["boom"]}, follow_redirects=False)
    _wait(runner.runs[2])
    assert sorted(f.name for f in store.iterdir()) == ["1.jsonl", "2.jsonl"]
    assert oct((store / "1.jsonl").stat().st_mode)[-3:] == "600"
    # a third run the process died under: header and two lines, no end record
    (store / "3.jsonl").write_text(json.dumps({"id": 3, "profile": "dogfood", "kind": "install", "what": "kobo",
                                               "started": 1.0}) + "\n"
                                   + json.dumps({"level": "head", "text": "kobo"}) + "\n"
                                   + json.dumps({"level": "info", "text": "kobo: connected (app 2, new)"}) + "\n"
                                   + '{"level": "info", "text": "half a ')          # died mid-write

    again = Runner(store)                                      # `stack ui` restarted
    site2 = TestClient(make_app(tmp_path / "p.yaml", again, perform=perform, preflight=lambda s, t: []))
    assert sorted(again.runs) == [1, 2, 3]
    assert again.runs[1].status == "done" and again.runs[2].status == "failed"
    assert [ln.text for ln in again.runs[1].lines][:2] == ["litellm", "litellm: connected (app 1)"]
    assert "stopped: litellm: provision failed twice" in again.runs[2].lines[-1].text
    r3 = again.runs[3]
    assert r3.status == "failed" and r3.what == "kobo" and r3.lines[-1].level == "fail"
    assert "stack ui stopped while this run was going" in r3.lines[-1].text and "press again" in r3.lines[-1].text
    assert [ln.text for ln in r3.lines][:2] == ["kobo", "kobo: connected (app 2, new)"]   # the half line is dropped
    page = site2.get("/runs/3").text
    assert "stack ui stopped while this run was going" in page and "hx-trigger" not in page
    assert "#3" in site2.get("/p/dogfood").text
    # the next run is numbered after what the files hold
    site2.post("/p/dogfood/install", data={"apps": ["litellm"]}, follow_redirects=False)
    assert 4 in again.runs
    _wait(again.runs[4])
    assert (store / "4.jsonl").exists() and again.runs[4].status == "done"
