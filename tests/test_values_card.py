"""The page asks four things (stack#12, after increment 2): the profile page
shows a card for the GitHub organization, the GCP project, the region and the
cluster until Gojira is installed. One press saves them on the profile (they
fill the stack file's tokens from then on) and writes the install's env file
with what follows: project/region/cluster, the roles the apps' contracts ask
for, the environment map, terraform on. The card reads the env file back, so
it shows what the file holds and never what a form remembered. Every test here
fails at ae032c8 (no card, no route, no profile fields, no tokens)."""

from fastapi.testclient import TestClient

from stack.profiles import Profile, Profiles
from stack.runs import Runner
from stack.ui import make_app
from tests.fakes import FakeGcloud, FakeGitHub, FakeKubectl

NS = "gojira"
STACK = '''gojira:
  kube_context: gke_{project}_{region}_{cluster}
cluster:
  name: "{cluster}"
  gcp_project: "{project}"
  location: "{region}"
apps:
  - name: litellm
    repo: tenishai/litellm-deploy
    image: ghcr.io/tenishai/litellm-deploy:v0.1.0@sha256:aaaa
    deploy_repo: "{org}/infra-litellm-deploy"
    contract:
      identity: { gcp_roles: [roles/aiplatform.user] }
      env: { VERTEX_PROJECT: "{project}" }
  - name: kobo
    repo: tenishai/kobo
    image: ghcr.io/tenishai/kobo:v1@sha256:bbbb
    deploy_repo: "{org}/infra-kobo-deploy"
    contract:
      identity: { gcp_roles: [roles/storage.admin, roles/aiplatform.user] }
'''
EXAMPLE = ("# Gojira install values\nPROJECT=my-gcp-project\nREGION=us-central1\n# the cluster\nCLUSTER=gojira-sandbox\n"
           "GOJIRA_GITHUB_APP_ID=1234567\nGOJIRA_GITHUB_APP_PEM=./github-app.pem\nGOJIRA_TERRAFORM=\n"
           "GOJIRA_ENVIRONMENT_CLUSTERS=\nGOJIRA_GRANTABLE_ROLES=\nGOJIRA_GRANTABLE_CLUSTER_ROLES=\nGOJIRA_CI_RUNNER=\n")
FOUR = {"org": "myorg", "gcp_project": "my-proj", "region": "us-central1", "cluster": "gojira-sandbox"}


def _site(tmp_path, *, with_values=False, env_exists=False):
    stack_file = tmp_path / "stack.yaml"
    stack_file.write_text(STACK)
    gojira = tmp_path / "gojira"
    gojira.mkdir()
    (gojira / "gojira.env.example").write_text(EXAMPLE)
    env_file = tmp_path / "cfg" / "gojira.env"
    if env_exists:
        env_file.parent.mkdir()
        env_file.write_text("PROJECT=old\nGOJIRA_GITHUB_APP_ID=5141957\nGOJIRA_GITHUB_APP_PEM=/keys/app.pem\nMY_OWN=kept\n")
    profiles = tmp_path / "profiles.yaml"
    book = Profiles()
    extra = FOUR if with_values else {}
    book.put(Profile(name="qa", stack_file=str(stack_file), env_file=str(env_file), gojira_repo=str(gojira), **extra))
    book.save(profiles)
    github = FakeGitHub(repos={
        "tenishai/litellm-deploy": {"gojira.yaml": "app: litellm\nidentity:\n  gsa: a@b\n  gcp_roles: [roles/aiplatform.user]\n"},
        "tenishai/kobo": {"gojira.yaml": "app: kobo\ncluster_roles: [kobo-room]\n"},
    })
    kubectl = FakeKubectl()   # no namespaces: an empty cluster
    app = make_app(profiles, Runner(), github=lambda **kw: github, kubectl=lambda **kw: kubectl,
                   gcloud=lambda **kw: FakeGcloud())
    return TestClient(app), profiles, env_file, github


def test_the_card_asks_the_four_and_the_stack_file_reads_only_with_them(tmp_path):
    client, profiles, env_file, _ = _site(tmp_path)
    r = client.get("/p/qa")
    assert r.status_code == 200
    # the file carries tokens and the profile has none: the page says which, instead of a Gojira strip
    assert "needs values for {org}" in r.text
    assert 'name="org"' in r.text and 'name="gcp_project"' in r.text and 'name="region"' in r.text and 'name="cluster"' in r.text
    assert "Save and write the env file" in r.text
    assert "not written yet" in r.text


def test_one_press_saves_the_four_and_writes_the_env_file_from_the_example(tmp_path):
    client, profiles, env_file, github = _site(tmp_path)
    r = client.post("/p/qa/values", data=FOUR, follow_redirects=False)
    assert r.status_code == 303, r.text
    p = Profiles.load(profiles).get("qa")
    assert (p.org, p.gcp_project, p.region, p.cluster) == ("myorg", "my-proj", "us-central1", "gojira-sandbox")
    text = env_file.read_text()
    assert text.startswith("# Gojira install values\nPROJECT=my-proj\nREGION=us-central1\n# the cluster\nCLUSTER=gojira-sandbox\n")
    assert "GOJIRA_TERRAFORM=1\n" in text
    assert "GOJIRA_ENVIRONMENT_CLUSTERS={dev: gojira-sandbox}\n" in text
    assert "GOJIRA_GRANTABLE_ROLES=roles/aiplatform.user,roles/storage.admin\n" in text
    assert "GOJIRA_GRANTABLE_CLUSTER_ROLES=kobo-room\n" in text
    assert "GOJIRA_CI_RUNNER=\n" in text    # the example's other knobs ride along, docs and all
    # the contracts were read from GitHub (no clone anywhere)
    assert "tenishai/litellm-deploy" in github.repos and "tenishai/kobo" in github.repos
    # the page now reads the stack file with the four and shows the env file's truth
    r = client.get("/p/qa")
    assert "gke_my-proj_us-central1_gojira-sandbox" in r.text
    assert "roles/aiplatform.user,roles/storage.admin" in r.text
    assert "not written yet" not in r.text
    assert "needs values for" not in r.text


def test_a_rewrite_keeps_what_the_app_card_and_the_operator_wrote(tmp_path):
    client, profiles, env_file, _ = _site(tmp_path, with_values=True, env_exists=True)
    r = client.post("/p/qa/values", data={**FOUR, "cluster": "sandbox-two"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    text = env_file.read_text()
    assert "GOJIRA_GITHUB_APP_ID=5141957\nGOJIRA_GITHUB_APP_PEM=/keys/app.pem\n" in text
    assert "MY_OWN=kept\n" in text
    assert text.startswith("PROJECT=my-proj\n") and "PROJECT=old" not in text
    assert "CLUSTER=sandbox-two\n" in text and "GOJIRA_ENVIRONMENT_CLUSTERS={dev: sandbox-two}\n" in text
    assert Profiles.load(profiles).get("qa").cluster == "sandbox-two"


def test_a_missing_value_or_env_file_is_a_sentence_not_a_write(tmp_path):
    client, profiles, env_file, _ = _site(tmp_path)
    r = client.post("/p/qa/values", data={**FOUR, "cluster": ""})
    assert r.status_code == 422 and "all four are needed" in r.text
    assert not env_file.exists()
    assert Profiles.load(profiles).get("qa").org == ""   # nothing saved either
    # no env file on the profile: the four are saved, the write is asked for again
    book = Profiles.load(profiles)
    book.put(book.get("qa").model_copy(update={"env_file": ""}))
    book.save(profiles)
    r = client.post("/p/qa/values", data=FOUR)
    assert r.status_code == 422 and "set the env file on the profile" in r.text
    assert Profiles.load(profiles).get("qa").org == "myorg"


def test_the_card_is_gone_once_gojira_is_installed(tmp_path):
    client, profiles, env_file, _ = _site(tmp_path, with_values=True, env_exists=True)
    k = FakeKubectl(ready={NS: (1, 1)})
    k.ns_set.add(NS)
    k.configmaps[(NS, "gojira-install")] = {"GOJIRA_VERSION": "v1.3.1"}
    app = make_app(profiles, Runner(), github=lambda **kw: FakeGitHub(), kubectl=lambda **kw: k,
                   gcloud=lambda **kw: FakeGcloud())
    r = TestClient(app).get("/p/qa")
    assert r.status_code == 200
    assert "is on this cluster" in r.text
    assert "Save and write the env file" not in r.text
