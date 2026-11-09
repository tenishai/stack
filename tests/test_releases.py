"""Releases: what the stack file could pin next, and the one line that pins it."""

import pytest

from stack.clients import ToolError
from stack.model import Stack
from stack.runs import Target, available_tags, set_image
from tests.fakes import FakeCrane

TEXT = (
    "cluster: {name: c, gcp_project: p, location: us-central1}\n"
    "apps:\n"
    "  - name: bab\n"
    "    repo: o/bab\n"
    "    image: r/agent-images/bab:v0.4.17   # keep this comment\n"
    "    source_image: ghcr.io/o/bab\n"
    "    deploy_repo: o/bab-deploy\n"
    "  - name: kobo\n"
    "    repo: o/kobo\n"
    "    image: r/agent-images/kobo@sha256:" + "a" * 64 + "\n"
    "    source_image: ghcr.io/o/kobo\n"
    "    deploy_repo: o/kobo-deploy\n"
)


def test_tags_are_versions_newest_first():
    crane = FakeCrane()
    crane.tags_by["ghcr.io/o/kobo"] = ["v0.3.9", "latest", "v0.3.58", "0.4.0", "abc1234", "v0.3.10", "v1"]
    stack = Stack.model_validate(__import__("yaml").safe_load(TEXT))
    assert available_tags(stack.apps[1], crane) == ["0.4.0", "v0.3.58", "v0.3.10", "v0.3.9"]
    assert available_tags(stack.apps[1], crane, limit=2) == ["0.4.0", "v0.3.58"]
    with pytest.raises(ToolError, match="no source_image"):
        available_tags(Stack.model_validate(__import__("yaml").safe_load(
            "cluster: {name: c, gcp_project: p, location: l}\napps: [{name: x, repo: o/x}]")).apps[0], crane)


def test_set_image_rewrites_one_line_and_keeps_the_rest(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text(TEXT)
    crane = FakeCrane(digests={"ghcr.io/o/kobo:v0.3.58": "sha256:" + "b" * 64})
    old, new = set_image(Target(stack_file=f), "kobo", "v0.3.58", crane)
    assert old.endswith("a" * 64) and new == "r/agent-images/kobo:v0.3.58@sha256:" + "b" * 64
    text = f.read_text()
    assert "    image: r/agent-images/kobo:v0.3.58@sha256:" + "b" * 64 + "   # v0.3.58, pinned by stack ui\n" in text
    assert "image: r/agent-images/bab:v0.4.17   # keep this comment" in text
    assert Stack.load(f).apps[1].image == new
    # a tag-pinned line (bab) rewrites the same way; the pin gains its digest
    crane.digests["ghcr.io/o/bab:v0.4.20"] = "sha256:" + "c" * 64
    set_image(Target(stack_file=f), "bab", "v0.4.20", crane)
    assert "image: r/agent-images/bab:v0.4.20@sha256:" + "c" * 64 in f.read_text()
    assert "keep this comment" not in f.read_text()


def test_set_image_refuses_before_touching_the_file(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text(TEXT)
    crane = FakeCrane()
    with pytest.raises(ToolError, match="manifest unknown"):
        set_image(Target(stack_file=f), "kobo", "v9", crane)
    with pytest.raises(ToolError, match="not in the stack file"):
        set_image(Target(stack_file=f), "nope", "v9", crane)
    f.write_text(TEXT.replace("    source_image: ghcr.io/o/kobo\n", ""))
    with pytest.raises(ToolError, match="no source_image"):
        set_image(Target(stack_file=f), "kobo", "v9", crane)
    assert "a" * 64 in f.read_text()
