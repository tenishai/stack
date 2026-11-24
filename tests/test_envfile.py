"""The env file as a form: lossless round-trip, loads under the real env.sh,
refuses a trailing comment and an indented comment the way env.sh does
(Handoff 01, slice 2, criteria 1-3)."""

import subprocess
import textwrap
from pathlib import Path

import pytest

from stack.clients import ToolError
from stack.envfile import (
    KNOWN_KNOBS,
    example_docs,
    get,
    parse_lines,
    read_env_file,
    save_env_file,
    validate,
)

ROOT = Path(__file__).parent.parent
GOJIRA = ROOT.parent / "gojira"
ENV_SH = GOJIRA / "scripts" / "env.sh"
EXAMPLE = GOJIRA / "gojira.env.example"
CI_SKIP = "gojira checkout not present (skips on CI)"

# A file exercising every thing a lossless write must keep: a header comment
# block, a comment line above a key, a blank line, an unknown key, a value with
# spaces, and a trailing newline.
ROUND_TRIP = """\
# header comment
# a second header line

PROJECT=acme-prod
# the cluster beneath it
CLUSTER=gojira-sandbox
REGION=us-central1
UNKNOWN_KEY=keep-me
GOJIRA_NAMESPACE_LABELS={example.com/team: sre}
GOJIRA_GITHUB_APP_PEM=./github-app.pem
"""


def test_round_trip_is_byte_identical(tmp_path):
    """Criterion 1: a file with every one of those is byte-identical after a
    read + save that changes none of the values it knows."""
    p = tmp_path / "gojira.env"
    p.write_text(ROUND_TRIP)
    # save the same values back (PROJECT/CLUSTER/REGION/labels/pem, untouched)
    out = save_env_file(p, {
        "PROJECT": "acme-prod", "CLUSTER": "gojira-sandbox", "REGION": "us-central1",
        "GOJIRA_NAMESPACE_LABELS": "{example.com/team: sre}",
        "GOJIRA_GITHUB_APP_PEM": "./github-app.pem",
    })
    assert p.read_text() == ROUND_TRIP
    assert out == ROUND_TRIP
    # the unknown key survived, in its place
    assert "UNKNOWN_KEY=keep-me" in p.read_text()


def test_round_trip_keeps_comments_blanks_order(tmp_path):
    p = tmp_path / "gojira.env"
    p.write_text(ROUND_TRIP)
    lines = parse_lines(p.read_text())
    kinds = [k for k, _ in lines]
    # a header comment, then a blank, then an assign, then a comment above CLUSTER
    assert kinds[0] == "comment"
    assert kinds[1] == "comment"
    assert kinds[2] == "blank"
    assert kinds[3] == "assign"      # PROJECT
    assert kinds[4] == "comment"     # above CLUSTER
    # and saving a value change keeps the comment lines in place
    save_env_file(p, {"PROJECT": "acme-prod"})
    after = p.read_text()
    assert after.startswith("# header comment")
    assert "\n\n" in after                          # the blank kept
    assert "GOJIRA_NAMESPACE_LABELS={example.com/team: sre}" in after


def test_save_changes_only_the_named_keys(tmp_path):
    p = tmp_path / "gojira.env"
    p.write_text(ROUND_TRIP)
    save_env_file(p, {"PROJECT": "other-project"})
    text = p.read_text()
    assert "PROJECT=other-project" in text
    assert "CLUSTER=gojira-sandbox" in text         # untouched
    assert "UNKNOWN_KEY=keep-me" in text           # unknown survives
    assert "# header comment" in text


def test_get_reads_values(tmp_path):
    p = tmp_path / "gojira.env"
    p.write_text(ROUND_TRIP)
    text = read_env_file(p)
    assert get(text, "PROJECT") == "acme-prod"
    assert get(text, "GOJIRA_NAMESPACE_LABELS") == "{example.com/team: sre}"
    assert get(text, "CLUSTER") == "gojira-sandbox"
    assert get(text, "NOT_THERE") == ""


def test_trailing_comment_is_refused(tmp_path):
    """Criterion 3: a `KEY=value # note` is refused on save, and the file on
    disk is unchanged — the way env.sh refuses it."""
    p = tmp_path / "gojira.env"
    p.write_text("PROJECT=acme-prod\nCLUSTER=gojira-sandbox\n")
    with pytest.raises(ToolError, match="comment after its value"):
        save_env_file(p, {"PROJECT": "acme-prod # note"})
    # the file was not touched
    assert p.read_text() == "PROJECT=acme-prod\nCLUSTER=gojira-sandbox\n"


def test_validate_refuses_trailing_comment_in_existing_file():
    with pytest.raises(ToolError, match="comment after its value"):
        validate("KEY=1   # why\n")
    # a `#` directly on a non-space char is still part of the value, not a comment
    validate("KEY=1#not-a-comment\n")


def test_validate_refuses_an_indented_comment(tmp_path):
    """Ember 06, defect 3: a `#` indented from column 0 is a comment to the
    writer and a refusal to env.sh (its skip is `''|'#'*`). validate and a
    save both refuse it; the file on disk is left untouched."""
    with pytest.raises(ToolError, match="not on its own line"):
        validate("PROJECT=acme\n   # indented\nREGION=us\n")
    p = tmp_path / "gojira.env"
    p.write_text("PROJECT=acme\n   # indented\nREGION=us\n")
    before = p.read_text()
    with pytest.raises(ToolError):
        save_env_file(p, {"PROJECT": "acme", "REGION": "us"})
    assert p.read_text() == before          # refused, the file is unchanged


def test_saving_a_valid_file_passes_validate(tmp_path):
    p = tmp_path / "gojira.env"
    p.write_text("PROJECT=acme\nCLUSTER=sandbox\n")
    # comment on its own line is fine
    save_env_file(p, {"PROJECT": "acme", "CLUSTER": "sandbox"})
    assert not p.read_text().splitlines()[-1].endswith("# x")


def test_clearing_a_knob_writes_an_empty_assignment(tmp_path):
    """Ember 06, defect 2: a key the file carries and the operator cleared
    becomes `KEY=` (env.sh reads it as unset); a key absent from the file and
    empty is not appended."""
    p = tmp_path / "gojira.env"
    p.write_text("GOJIRA_CI_RUNNER=spark\nPROJECT=acme\n")
    save_env_file(p, {"GOJIRA_CI_RUNNER": "", "PROJECT": "acme"})
    text = p.read_text()
    assert "GOJIRA_CI_RUNNER=" in text
    assert "GOJIRA_CI_RUNNER=spark" not in text
    assert "PROJECT=acme" in text


# --- criterion 2: the saved file loads under gojira's scripts/env.sh ----------

@pytest.mark.skipif(not ENV_SH.exists(), reason=CI_SKIP)
def test_saved_file_loads_through_env_sh(tmp_path):
    """The form's output, sourced through gojira's real env.sh, exports exactly
    the values the form set. Sources the real file (not a transcription of its
    rule, which a change to env.sh would bypass)."""
    p = tmp_path / "gojira.env"
    values = {"PROJECT": "acme-prod", "REGION": "us-central1",
              "CLUSTER": "gojira-sandbox", "GOJIRA_NAMESPACE_LABELS": "{example.com/team: sre}"}
    save_env_file(p, values)
    script = tmp_path / "consumer.sh"
    script.write_text(textwrap.dedent(f"""\
        export GOJIRA_ENV={p}
        . {ENV_SH}
        if [ $? -ne 0 ]; then echo "env refused" >&2; exit 1; fi
        echo "PROJECT=$PROJECT REGION=$REGION CLUSTER=$CLUSTER LABELS=$GOJIRA_NAMESPACE_LABELS"
    """))
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "PROJECT=acme-prod" in proc.stdout
    assert "REGION=us-central1" in proc.stdout
    assert "CLUSTER=gojira-sandbox" in proc.stdout
    assert "LABELS={example.com/team: sre}" in proc.stdout


@pytest.mark.skipif(not ENV_SH.exists(), reason=CI_SKIP)
def test_env_sh_refuses_a_trailing_comment(tmp_path):
    """The negative Ember will run: a file with a trailing comment, sourced
    through the real env.sh, is refused (non-zero, the key named)."""
    p = tmp_path / "gojira.env"
    p.write_text("KEY=1   # why\n")
    # A real consumer script: source env.sh, gate on $? the way gojira's own
    # scripts do, and only then do their work.
    script = tmp_path / "consumer.sh"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        export GOJIRA_ENV={p}
        . {ENV_SH}
        if [ $? -ne 0 ]; then
          echo "env refused" >&2
          exit 1
        fi
        echo SHOULD-NOT-REACH
    """))
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "env refused" in proc.stderr
    assert "SHOULD-NOT-REACH" not in proc.stdout
    assert "KEY" in proc.stderr


@pytest.mark.skipif(not ENV_SH.exists(), reason=CI_SKIP)
def test_env_sh_refuses_an_indented_comment(tmp_path):
    """Ember 06, defect 3, against the real file: an indented `#` is refused by
    env.sh, so a writer that treated it as a comment would save a file Gojira
    refuses at provision time."""
    p = tmp_path / "gojira.env"
    p.write_text("PROJECT=acme\n   # indented\nREGION=us\n")
    script = tmp_path / "consumer.sh"
    script.write_text(textwrap.dedent(f"""\
        export GOJIRA_ENV={p}
        . {ENV_SH}
        if [ $? -ne 0 ]; then echo "env refused" >&2; exit 1; fi
        echo SHOULD-NOT-REACH
    """))
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "SHOULD-NOT-REACH" not in proc.stdout


# --- criterion 1 (docs): the example's whole paragraph, not a fragment --------

def test_example_docs_are_the_whole_paragraph():
    """Ember 06, item 6: a key's doc is the whole comment block above it, in
    reading order (a wrapped comment is not its last line); a `---` banner is a
    divider, not a doc, so a key under a banner only gets ""."""
    example = "\n".join([
        "# --- required -----------------------------------------------------------------",
        "PROJECT=acme",
        "REGION=us-central1",
        "# The GKE cluster Gojira runs in. terraform/ creates gojira-sandbox; for an",
        "# existing cluster set its name and GOJIRA_PREPARE_PROJECT=1 below.",
        "CLUSTER=gojira-sandbox",
        "# Your GitHub App: numeric id + downloaded key.",
        "GOJIRA_GITHUB_APP_ID=1234567",
        "GOJIRA_GITHUB_APP_PEM=./github-app.pem",
        "",
        "# --- the cluster underneath: pick one, or neither -----------------------------",
        "# 1 = make install creates the cluster with terraform/ (first time, about",
        "# ten minutes). Leave empty if you run terraform yourself.",
        "GOJIRA_TERRAFORM=",
    ])
    docs = example_docs(example)
    # CLUSTER: both wrapped lines, in reading order, joined into one sentence
    assert docs["CLUSTER"] == ("The GKE cluster Gojira runs in. terraform/ creates "
                               "gojira-sandbox; for an existing cluster set its name "
                               "and GOJIRA_PREPARE_PROJECT=1 below.")
    # APP_ID carries the shared paragraph; PEM has no block directly above it
    # (APP_ID sits right above), so it gets none — the doc is what is directly above
    assert docs["GOJIRA_GITHUB_APP_ID"] == "Your GitHub App: numeric id + downloaded key."
    assert docs["GOJIRA_GITHUB_APP_PEM"] == ""
    # TERRAFORM: its wrapped paragraph, in reading order
    assert docs["GOJIRA_TERRAFORM"] == ("1 = make install creates the cluster with terraform/ "
                                        "(first time, about ten minutes). Leave empty if you "
                                        "run terraform yourself.")
    # PROJECT and REGION sit under the banner only: no doc
    assert docs["PROJECT"] == ""
    assert docs["REGION"] == ""


def test_example_docs_against_the_real_example():
    if not EXAMPLE.exists():
        pytest.skip(CI_SKIP)
    docs = example_docs(EXAMPLE.read_text())
    c = docs.get("CLUSTER", "")
    assert "The GKE cluster Gojira runs in" in c
    assert "existing cluster set its name" in c
    # every knob the page edits gets a doc from the example (or "")
    for key, _ in KNOWN_KNOBS:
        assert key in docs


def test_known_knobs_are_a_known_set():
    # the page edits exactly these; the set is stable
    assert {"PROJECT", "REGION", "CLUSTER", "GOJIRA_GITHUB_APP_ID"} <= {k for k, _ in KNOWN_KNOBS}