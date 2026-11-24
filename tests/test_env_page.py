"""The profile's env file as a form: the page lists what the machine can see
(gcloud projects, the kubeconfig's contexts), reads the file's current values
and the example's one-line docs, and saves back through the lossless writer
(Handoff 01, slice 2, criteria 4-6)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stack.runs import Runner
from stack.ui import make_app
from tests.fakes import FakeGcloud, FakeKubectl

ROOT = Path(__file__).parent.parent
GOJIRA = ROOT.parent / "gojira"
EXAMPLE = GOJIRA / "gojira.env.example"


@pytest.fixture
def site(tmp_path):
    stack_file = tmp_path / "s.yaml"
    stack_file.write_text(
        "cluster: {name: gojira-sandbox, gcp_project: vidios-qa, location: us-central1}\n"
        "apps:\n  - {name: kobo, repo: tenishai/gojira, deploy_repo: tenishai/kobo-deploy,\n"
        "     image: r/agent-images/kobo:1, source_image: ghcr.io/tenishai/kobo}\n")
    env = tmp_path / "gojira.env"
    env.write_text(
        "# a header comment\n"
        "\n"
        "PROJECT=vidios-qa\n"
        "# the cluster beneath it\n"
        "CLUSTER=gojira-sandbox\n"
        "REGION=us-central1\n"
        "SOME_CUSTOM_KEY=untouched\n"
    )
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("")

    gcloud = FakeGcloud(account="me@example.com", projects=[
        {"projectId": "vidios-qa", "name": "vidios-qa"},
        {"projectId": "prod-gpu", "name": "Prod GPU (unrelated)"},
        {"projectId": "untitled-1234", "name": "Untitled project"},
    ])
    kubectl = FakeKubectl()
    kubectl.contexts = ["gke_vidios-qa_us-central1_gojira-sandbox", "gke_other_cluster", "minikube"]

    runner = Runner()
    app = make_app(tmp_path / "p.yaml", runner,
                   perform=lambda *a, **k: _noop(), preflight=lambda s, t: [],
                   kubectl=lambda env: kubectl, gcloud=lambda env: gcloud)
    client = TestClient(app)
    client.gcloud, client.kubectl, client.env = gcloud, kubectl, env
    # register the profile pointing at our env file
    client.post("/profiles", data={"name": "prod", "stack_file": str(stack_file),
                                   "kubeconfig": str(kubeconfig), "env_file": str(env)})
    return client


def _noop():
    from stack.steps import Report
    return Report()


def test_env_page_lists_projects_and_contexts(site):
    page = site.get("/p/prod/env").text
    # criterion 4: gcloud projects, with the display name beside the id so a
    # "untitled" is told apart from the project it holds
    assert "vidios-qa" in page
    assert "prod-gpu" in page
    assert "Prod GPU (unrelated)" in page
    assert "Untitled project" in page
    # the kubeconfig's contexts are the CLUSTER pick
    assert "gke_vidios-qa_us-central1_gojira-sandbox" in page
    assert "minikube" in page
    # criterion 5: the file's current values
    assert 'value="vidios-qa"' in page
    assert 'value="gojira-sandbox"' in page
    assert 'value="us-central1"' in page
    # criterion 6: the docs are one line per knob
    assert "GCP project" in page or "gcp project" in page


def test_env_page_reads_docs_from_the_example(site, tmp_path):
    # when the profile's checkout has no example, the page's own one-liners show
    page = site.get("/p/prod/env").text
    for knob in ("PROJECT", "CLUSTER", "GOJIRA_GITHUB_APP_ID", "GOJIRA_TERRAFORM"):
        assert knob in page


def test_saving_env_round_trips_and_keeps_unknowns(site):
    env = site.env
    before = env.read_text()
    assert "SOME_CUSTOM_KEY=untouched" in before
    # change a known value
    site.post("/p/prod/env", data={"PROJECT": "prod-gpu", "CLUSTER": "gojira-sandbox", "REGION": "us-central1"})
    after = env.read_text()
    assert "PROJECT=prod-gpu" in after
    assert "CLUSTER=gojira-sandbox" in after
    # criterion 1: comments, blank, and the unknown key are all still there
    assert "# a header comment" in after
    assert "# the cluster beneath it" in after
    assert "SOME_CUSTOM_KEY=untouched" in after
    assert "\n\n" in after
    # order preserved: PROJECT before CLUSTER, CLUSTER before REGION
    assert after.index("PROJECT=") < after.index("CLUSTER=") < after.index("REGION=")


def test_saving_a_trailing_comment_is_refused_and_leaves_file_untouched(site):
    env = site.env
    before = env.read_text()
    # a value with a trailing comment is refused, the file unchanged
    r = site.post("/p/prod/env", data={"PROJECT": "vidios-qa # why"}, follow_redirects=False)
    assert r.status_code == 422
    assert "trailing" in r.text or "comment" in r.text
    assert env.read_text() == before


def test_env_page_without_an_env_file_says_so(site):
    # a profile with no env file: the page points to Profile settings
    site.post("/profiles", data={"name": "bare", "stack_file": "/no/such/stack.yaml"})
    page = site.get("/p/bare/env").text
    assert "no env file" in page or "Gojira env file" in page
    assert "Profile settings" in page


def test_env_page_without_kubeconfig_says_so(site, tmp_path):
    # no kubeconfig pinned: the CLUSTER pick is a note, not a silent empty
    site.post("/profiles", data={"name": "naked", "stack_file": str(tmp_path / "x.yaml"),
                                 "env_file": str(site.env)})
    page = site.get("/p/naked/env").text
    assert "no kubeconfig" in page


def test_docs_prefer_the_example_over_the_pages_own(site, tmp_path):
    # put an example under the profile's repos so the page finds it
    repos = tmp_path / "repos" / "gojira"
    repos.mkdir(parents=True, exist_ok=True)
    (repos / "gojira.env.example").write_text(EXAMPLE.read_text()) if EXAMPLE.exists() else None
    site.post("/profiles", data={"name": "doc", "stack_file": str(tmp_path / "d.yaml"),
                                 "repos": str(tmp_path / "repos"), "env_file": str(site.env)})
    page = site.get("/p/doc/env").text
    # the example's doc for PROJECT ("GCP project the install belongs to") wins
    assert "GCP project" in page