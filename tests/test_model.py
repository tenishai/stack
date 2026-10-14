"""The stack file: what it accepts, what it refuses, and the dogfood example."""

from pathlib import Path

import pytest
import yaml

from stack.model import App, Source, Stack

ROOT = Path(__file__).parent.parent


def test_the_dogfood_example_loads_in_dependency_order():
    s = Stack.load(ROOT / "examples" / "dogfood.yaml")
    assert [a.name for a in s.apps] == ["litellm", "cabal", "liminal", "kobo"]
    assert s.cluster.ar_repo == "us-central1-docker.pkg.dev/shaz-learning/apps"
    assert s.cluster.gke_cluster == "gojira-sandbox"
    kobo = s.apps[-1]
    assert kobo.namespace == "kobo"
    assert kobo.cluster_objects == ["k8s/cluster/rbac.yaml"]
    tok = kobo.secrets["kobo-cabal"]["CABAL_OPERATOR_TOKEN"]
    assert (tok.kind, tok.cabal_sub, tok.cabal_scope) == ("cabal-token", "service:kobo", "operator")
    assert kobo.secrets["kobo-github"]["private-key.pem"].path == Path("~/tenish-room.pem").expanduser()
    # the file travels: its config files are relative to it, its keys generated and shared by name
    cabal = s.apps[1]
    assert cabal.configmaps["cabal-config"]["identity.toml"].path == Path("dogfood/cabal-identity.toml")
    assert (ROOT / "examples" / "dogfood" / "cabal-identity.toml").is_file()
    litellm_key = s.apps[0].secrets["litellm-secrets"]["LITELLM_MASTER_KEY"]
    assert (litellm_key.gen_bytes, litellm_key.gen_name) == (32, "litellm-master-key")
    assert cabal.secrets["cabal-secrets"]["LITELLM_MASTER_KEY"].gen_name == "litellm-master-key"
    assert cabal.secrets["cabal-secrets"]["CABAL_MASTER_KEY"].gen_name == ""


@pytest.mark.parametrize("text,kind,arg", [
    ("file:~/x", "file", "~/x"),
    ("generate:32", "generate", "32"),
    ("generate:32:litellm-master-key", "generate", "32:litellm-master-key"),
    ("gojira-token", "gojira-token", ""),
    ("template:/etc/t.yaml", "template", "/etc/t.yaml"),
    ("cabal-token:service:liminal:secrets-fetch", "cabal-token", "service:liminal:secrets-fetch"),
    ("literal:dev", "literal", "dev"),
])
def test_value_sources_parse(text, kind, arg):
    s = Source.parse(text)
    assert (s.kind, s.arg) == (kind, arg)


@pytest.mark.parametrize("text", ["~/x", "generate:lots", "generate:32:Not_A_Name", "cabal-token:nocolon",
                                  "env:HOME", ""])
def test_a_value_that_is_not_a_source_is_refused(text):
    with pytest.raises(ValueError):
        Source.parse(text)


def test_an_app_needing_a_cabal_token_must_come_after_cabal():
    doc = {
        "cluster": {"name": "c", "gcp_project": "p", "location": "us-central1"},
        "apps": [
            {"name": "liminal", "repo": "o/liminal",
             "secrets": {"s": {"T": "cabal-token:service:liminal:secrets-fetch"}}},
            {"name": "cabal", "repo": "o/cabal", "bootstrap": "cabal"},
        ],
    }
    with pytest.raises(ValueError, match="bootstrap: cabal"):
        Stack.model_validate(doc)
    doc["apps"].reverse()
    assert [a.name for a in Stack.model_validate(doc).apps] == ["cabal", "liminal"]


def test_names_are_dns_labels_and_unique():
    with pytest.raises(ValueError):
        App(name="Not_Valid", repo="o/r")
    doc = {"cluster": {"name": "c", "gcp_project": "p", "location": "l"},
           "apps": [{"name": "a", "repo": "o/a"}, {"name": "a", "repo": "o/b"}]}
    with pytest.raises(ValueError, match="duplicate"):
        Stack.model_validate(doc)


def test_the_example_holds_no_secret_values():
    text = (ROOT / "examples" / "dogfood.yaml").read_text()
    for obj in yaml.safe_load(text)["apps"]:
        for keys in list(obj.get("secrets", {}).values()) + list(obj.get("configmaps", {}).values()):
            for value in keys.values():
                assert Source.parse(value).kind != "literal", "a literal in a Secret is a value in git"
