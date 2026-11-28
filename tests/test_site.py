"""The page asks four things (stack#12, after increment 2): org, project, region
and cluster. The stack file carries them as tokens the profile fills; the env
file Gojira installs from is written from them and from what the stack file's
apps ask for. Every test here fails at ae032c8 (no stack.site, Stack.load took
no values, the env file was typed by hand)."""

import subprocess
from pathlib import Path

import pytest
import yaml

from stack.model import Stack
from stack.site import DERIVED_KEYS, env_values, fill, write_env
from tests.fakes import FakeGitHub

ROOT = Path(__file__).parent.parent
GOJIRA = ROOT.parent / "gojira"
EXAMPLE = GOJIRA / "gojira.env.example"
ENV_SH = GOJIRA / "scripts" / "env.sh"
FOUR = {"org": "myorg", "project": "my-proj", "region": "us-central1", "cluster": "gojira-sandbox"}


def test_fill_replaces_the_four_and_names_what_is_missing():
    text = "ctx: gke_{project}_{region}_{cluster}\nrepo: \"{org}/x\"\ntoken: \"{gojira_token}\"\n"
    out = fill(text, FOUR)
    assert out == "ctx: gke_my-proj_us-central1_gojira-sandbox\nrepo: \"myorg/x\"\ntoken: \"{gojira_token}\"\n"
    with pytest.raises(ValueError, match=r"needs values for \{org\}, \{cluster\}"):
        fill("{cluster} {org} {cluster}", {"project": "p"})   # named in the file's order of the four, not of appearance
    # a late token is left for the installer unless the caller says it must be filled now
    with pytest.raises(ValueError, match=r"\{gojira_token\}"):
        fill("{gojira_token}", {}, allow=())
    # an empty value is no value
    with pytest.raises(ValueError, match=r"\{org\}"):
        fill("{org}", {"org": ""})


def test_the_stranger_file_loads_only_with_the_four_and_then_holds_no_token():
    path = ROOT / "examples" / "stack.yaml"
    with pytest.raises(ValueError, match=r"\{org\}"):
        Stack.load(path)
    s = Stack.load(path, FOUR)
    assert s.gojira.kube_context == "gke_my-proj_us-central1_gojira-sandbox"
    assert (s.cluster.gcp_project, s.cluster.location, s.cluster.name) == ("my-proj", "us-central1", "gojira-sandbox")
    for app in s.apps:
        assert app.deploy_repo == f"myorg/infra-{app.name}-deploy"
    assert s.apps[0].contract["env"]["VERTEX_PROJECT"] == "my-proj"
    assert "acme" not in path.read_text(), "the example carries tokens, not a sample org to edit"
    # the kobo template carries the same four plus the App's two, nothing to edit by hand
    tmpl = (ROOT / "examples" / "stack" / "kobo-config.template.yaml").read_text()
    for token in ("{org}", "{project}", "{app_id}", "{installation_id}", "{gojira_token}"):
        assert token in tmpl, token
    assert "acme" not in tmpl and "<app id>" not in tmpl
    doc = yaml.safe_load(fill(tmpl, {**FOUR, "app_id": "7", "installation_id": "9", "gojira_token": "t"}))
    assert doc["org_repo"] == "myorg/kobo-config" and doc["gcp_project"] == "my-proj"
    assert doc["github_app_id"] == "7" and doc["github_app_installation_id"] == "9"


def _stack(apps):
    return Stack.model_validate({"cluster": {"name": "gojira-sandbox", "gcp_project": "my-proj",
                                             "location": "us-central1"}, "apps": apps})


def test_env_values_are_the_four_plus_what_the_apps_ask_for():
    """Roles the way Gojira will see them: the upstream's contract with the stack
    file's contract: over it (identity.gcp_roles from the contract replaces the
    upstream's, as deploy_repo_files merges them); cluster_roles the same; every
    environment named -> this install's one cluster; terraform makes the cluster."""
    gh = FakeGitHub(repos={
        "tenishai/litellm-deploy": {"gojira.yaml": "app: litellm\nidentity:\n  gsa: x@other\n  gcp_roles: [roles/aiplatform.user]\n"},
        "tenishai/cabal": {"gojira.yaml": "app: cabal\n"},
        "tenishai/kobo": {"gojira.yaml": "app: kobo\ncluster_roles: [kobo-room]\nidentity:\n  gcp_roles: [roles/storage.admin]\n"},
    })
    s = _stack([
        {"name": "litellm", "repo": "tenishai/litellm-deploy", "image": "r/l:1", "deploy_repo": "myorg/infra-litellm-deploy"},
        {"name": "cabal", "repo": "tenishai/cabal", "image": "r/c:1", "deploy_repo": "myorg/infra-cabal-deploy",
         "environment": "prod"},
        {"name": "kobo", "repo": "tenishai/kobo", "image": "r/k:1", "deploy_repo": "myorg/infra-kobo-deploy",
         "contract": {"identity": {"gcp_roles": ["roles/iam.serviceAccountAdmin", "roles/aiplatform.user"]}}},
    ])
    vals = env_values(s, FOUR, gh)
    assert vals == {
        "PROJECT": "my-proj", "REGION": "us-central1", "CLUSTER": "gojira-sandbox",
        "GOJIRA_TERRAFORM": "1",
        "GOJIRA_ENVIRONMENT_CLUSTERS": "{dev: gojira-sandbox, prod: gojira-sandbox}",
        # kobo's contract replaced the upstream's storage.admin; litellm's upstream role stays
        "GOJIRA_GRANTABLE_ROLES": "roles/aiplatform.user,roles/iam.serviceAccountAdmin",
        "GOJIRA_GRANTABLE_CLUSTER_ROLES": "kobo-room",
    }
    assert tuple(vals) == DERIVED_KEYS
    # a clone under the repos dir is read before GitHub (what deploy_repo_files does)


def test_env_values_prefer_a_clone_and_survive_a_repo_without_a_contract(tmp_path):
    (tmp_path / "kobo").mkdir()
    (tmp_path / "kobo" / "gojira.yaml").write_text("cluster_roles: [from-clone]\n")
    s = _stack([{"name": "kobo", "repo": "o/kobo"}, {"name": "x", "repo": "o/x"}])
    vals = env_values(s, FOUR, FakeGitHub(repos={"o/kobo": {"gojira.yaml": "cluster_roles: [from-github]\n"}}),
                      repo_root=tmp_path)
    assert vals["GOJIRA_GRANTABLE_CLUSTER_ROLES"] == "from-clone"
    assert vals["GOJIRA_GRANTABLE_ROLES"] == ""


def test_write_env_starts_from_the_example_and_keeps_what_the_card_wrote(tmp_path):
    """A fresh file is the example with the derived knobs filled, every doc comment
    kept; a second write over a file carrying the App's two lines keeps them."""
    example = ("# header\nPROJECT=my-gcp-project\nREGION=us-central1\n# the cluster\nCLUSTER=gojira-sandbox\n"
               "GOJIRA_GITHUB_APP_ID=1234567\nGOJIRA_GITHUB_APP_PEM=./github-app.pem\nGOJIRA_TERRAFORM=\n"
               "GOJIRA_ENVIRONMENT_CLUSTERS=\nGOJIRA_GRANTABLE_ROLES=\nGOJIRA_GRANTABLE_CLUSTER_ROLES=\n"
               "GOJIRA_CI_RUNNER=\n")
    derived = {"PROJECT": "my-proj", "REGION": "us-central1", "CLUSTER": "gojira-sandbox", "GOJIRA_TERRAFORM": "1",
               "GOJIRA_ENVIRONMENT_CLUSTERS": "{dev: gojira-sandbox}", "GOJIRA_GRANTABLE_ROLES": "roles/aiplatform.user",
               "GOJIRA_GRANTABLE_CLUSTER_ROLES": "kobo-room"}
    p = tmp_path / "cfg" / "gojira.env"
    text = write_env(p, derived, example)
    assert text.startswith("# header\nPROJECT=my-proj\n") and "# the cluster\nCLUSTER=gojira-sandbox\n" in text
    assert "GOJIRA_TERRAFORM=1\n" in text and "GOJIRA_GRANTABLE_ROLES=roles/aiplatform.user\n" in text
    assert "GOJIRA_CI_RUNNER=\n" in text          # an unrelated knob keeps the example's value
    assert "GOJIRA_GITHUB_APP_ID=1234567" in text  # the example's sample id until the card writes the real one
    # the card writes the App; a rewrite of the derived knobs keeps it
    from stack.envfile import save_env_file
    save_env_file(p, {"GOJIRA_GITHUB_APP_ID": "5141957", "GOJIRA_GITHUB_APP_PEM": "/keys/app.pem"})
    again = write_env(p, {**derived, "GOJIRA_GRANTABLE_ROLES": "roles/aiplatform.user,roles/storage.admin"}, example)
    assert "GOJIRA_GITHUB_APP_ID=5141957\nGOJIRA_GITHUB_APP_PEM=/keys/app.pem\n" in again
    assert "GOJIRA_GRANTABLE_ROLES=roles/aiplatform.user,roles/storage.admin\n" in again
    assert again.count("PROJECT=") == 1


@pytest.mark.skipif(not (EXAMPLE.exists() and ENV_SH.exists()), reason="needs the gojira checkout beside this one")
def test_the_written_file_loads_through_the_real_env_sh(tmp_path):
    p = tmp_path / "gojira.env"
    write_env(p, {"PROJECT": "my-proj", "REGION": "us-central1", "CLUSTER": "gojira-sandbox", "GOJIRA_TERRAFORM": "1",
                  "GOJIRA_ENVIRONMENT_CLUSTERS": "{dev: gojira-sandbox}", "GOJIRA_GRANTABLE_ROLES": "roles/aiplatform.user",
                  "GOJIRA_GRANTABLE_CLUSTER_ROLES": "kobo-room"}, EXAMPLE.read_text())
    out = subprocess.run(["bash", "-c", f'GOJIRA_ENV="{p}" . "{ENV_SH}" && echo "$PROJECT|$GOJIRA_TERRAFORM|'
                          '$GOJIRA_ENVIRONMENT_CLUSTERS|$GOJIRA_GRANTABLE_ROLES|$GOJIRA_GRANTABLE_CLUSTER_ROLES"'],
                         capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "my-proj|1|{dev: gojira-sandbox}|roles/aiplatform.user|kobo-room"
