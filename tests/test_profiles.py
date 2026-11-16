"""Profiles: pointers to an install, never its contents."""

from pathlib import Path

import pytest

from stack.profiles import Profile, Profiles


def test_a_profile_pins_the_environment_and_expands_its_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = Profile(name="prod", stack_file="~/stack/prod.yaml", kubeconfig="~/.kube-prod/config",
                cloud_account="me@example.com", repos="~/src", cbl="~/cabal/.venv/bin/cbl")
    assert p.env() == {"KUBECONFIG": str(tmp_path / ".kube-prod/config"), "CLOUDSDK_CORE_ACCOUNT": "me@example.com"}
    t = p.target()
    assert t.stack_file == tmp_path / "stack/prod.yaml"
    assert t.repos == tmp_path / "src"
    assert t.cbl == str(tmp_path / "cabal/.venv/bin/cbl")
    assert t.env == p.env()
    # nothing pinned when nothing is set: the operator's defaults apply
    assert Profile(name="dev", stack_file="x.yaml").env() == {}
    assert Profile(name="dev", stack_file="x.yaml").target().repos is None


def test_the_file_round_trips_and_put_replaces_by_name(tmp_path):
    path = tmp_path / "cfg" / "profiles.yaml"
    assert Profiles.load(path).profiles == []          # no file yet is an empty list, not an error
    book = Profiles()
    book.put(Profile(name="prod", stack_file="prod.yaml"))
    book.put(Profile(name="sandbox", stack_file="sb.yaml", kubeconfig="kc"))
    book.put(Profile(name="prod", stack_file="prod2.yaml"))
    book.save(path)
    again = Profiles.load(path)
    assert [p.name for p in again.profiles] == ["prod", "sandbox"]
    assert again.get("prod").stack_file == "prod2.yaml"
    assert "kubeconfig: kc" in path.read_text() and "cloud_account" not in path.read_text()
    assert again.remove("prod") and not again.remove("prod")
    assert again.get("prod") is None


def test_names_are_labels_and_a_stack_file_is_required():
    with pytest.raises(ValueError):
        Profile(name="my prod", stack_file="x.yaml")
    with pytest.raises(ValueError):
        Profile(name="prod", stack_file="  ")
    assert isinstance(Profile(name="prod", stack_file="x.yaml").target().stack_file, Path)
