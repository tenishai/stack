"""The install's gojira.env, read and written losslessly (stack#12 increment 2).

The env file is the one values file Gojira's scripts load (`scripts/env.sh`
sources it). The page edits it as a form, so the write-back must keep
everything it did not touch: comments on their own lines, blank lines, key
order, and keys stack does not know. It also must load the same way env.sh
loads it — including refusing a comment that trails a value, the
`KEY=1   # why` shape env.sh refuses.
"""

from __future__ import annotations

import re

from stack.clients import ToolError

# The knobs the page edits, in the order gojira.env.example presents them.
# Each maps the env key to the one-line doc taken from gojira.env.example (the
# source of truth; a Gojira release may add or reword it, so this list is the
# page's, not a copy of the docs). The key order here is the form's order.
KNOWN_KNOBS: list[tuple[str, str]] = [
    ("PROJECT", "The GCP project the install belongs to."),
    ("REGION", "The region the cluster is in."),
    ("CLUSTER", "The GKE cluster Gojira runs in (terraform/ creates gojira-sandbox)."),
    ("GOJIRA_GITHUB_APP_ID", "Your GitHub App's numeric id (docs/github-app-setup.md)."),
    ("GOJIRA_GITHUB_APP_PEM", "Path to the GitHub App's downloaded private key."),
    ("GOJIRA_TERRAFORM", "1 = make install creates the cluster with terraform/; empty = you run terraform yourself."),
    ("GOJIRA_PREPARE_PROJECT", "1 = run prepare-project.sh first (brownfield); empty when terraform/ built the project."),
    ("GOJIRA_DNS_ENDPOINT", "1 = reach the cluster over GKE's IAM-authenticated DNS endpoint."),
    ("GOJIRA_CLUSTER_ACCESS", "internal = this machine is inside the cluster's network; empty = dns."),
    ("GOJIRA_HOSTNAME", "A hostname provisions a static IP, managed cert, IAP client and ingress."),
    ("GOJIRA_IAP_MEMBERS", "Who may pass IAP, comma-separated: domain:,user:."),
    ("GOJIRA_ALLOWED_DOMAIN", "Gojira's own allowlist behind IAP (auth on when set and GOJIRA_HOSTNAME is set)."),
    ("GOJIRA_ALLOWED_EMAILS", "Gojira's own allowlist behind IAP (auth on when set and GOJIRA_HOSTNAME is set)."),
    ("GOJIRA_NAMESPACE_LABELS", "Labels stamped on every namespace Gojira creates (YAML flow map)."),
    ("GOJIRA_DEFAULT_ARCH", "The architecture apps are built for (empty = amd64)."),
    ("GOJIRA_DEFAULT_NODE_SELECTOR", "How your clusters provide that arch (nodeSelector; empty = none)."),
    ("GOJIRA_ENVIRONMENT_CLUSTERS", "The cluster each environment name means (YAML flow map)."),
    ("GOJIRA_GRANTABLE_ROLES", "Project roles an app may declare (comma list; empty = none)."),
    ("GOJIRA_GRANTABLE_CLUSTER_ROLES", "ClusterRoles an app may bind to (comma list; empty = refused)."),
    ("GOJIRA_REPO_VARIABLES", "Values a gojira.yaml may reference (YAML flow map)."),
    ("GOJIRA_SIZING_AGENT_IMAGE", "An image of the sizing agent; enables the Clusters page button."),
    ("GOJIRA_OBSERVE_JOB_IMAGE", "A digest-pinned argus build the observe step runs."),
    ("GOJIRA_OBSERVE_CLUSTERS", "Comma-separated clusters whose apps argus observes."),
    ("GOJIRA_DRIFT_ENABLED", "Central drift detector (docs/design.md)."),
    ("GOJIRA_CI_RUNNER", "Self-hosted runner label (empty = GitHub's hosted runners)."),
]
KNOWN_KEYS = {k for k, _ in KNOWN_KNOBS}

# env.sh's refusal, mirrored: whitespace then `#` after a value (the `KEY=1 # why`
# shape). A bare `#` with no leading space is not a comment to env.sh.
_TRAILING_COMMENT = re.compile(r"[ \t]#")
_LINE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


def read_env_file(path) -> str:
    """The file's text, or "" when it is not there yet (the page starts a fresh
    one from the example)."""
    from pathlib import Path

    p = Path(path).expanduser()
    return p.read_text() if p.exists() else ""


def parse_lines(text: str) -> list[tuple[str, str]]:
    """The file as an ordered list of (kind, payload) lines.

    kind is one of "comment", "blank", or "assign"; payload is the line's text
    (comment/blank) or the value (assign). Nothing is dropped, so the writer
    can round-trip byte-for-byte. A line that is neither a comment, a blank nor
    a `KEY=value` is kept as an "other" so it is never silently lost."""
    out = []
    for line in text.split("\n"):
        if line == "":
            out.append(("blank", ""))
        elif line.lstrip().startswith("#"):
            out.append(("comment", line))
        elif _LINE.match(line):
            out.append(("assign", line))
        else:
            out.append(("other", line))
    return out


def _render(lines: list[tuple[str, str]]) -> str:
    # A trailing newline in the text yields a final "blank" line; drop it so we
    # do not grow a blank every round-trip, but keep an explicit one if there is
    # content after it.
    text = "\n".join(p for _, p in lines)
    # preserve a single trailing newline when the source had one (a real env
    # file ends in a newline; the empty final "blank" is the split artifact)
    if text.endswith("\n"):
        text = text[:-1]
    return text + "\n"


def get(text: str, key: str) -> str:
    """The value of `key`, or "" when it is not set."""
    for kind, payload in parse_lines(text):
        if kind != "assign":
            continue
        m = _LINE.match(payload)
        if m and m.group("key") == key:
            return m.group("value")
    return ""


def get_all(text: str, keys: list[tuple[str, str]] | None = None) -> dict[str, str]:
    """The value of every key in `keys` (default the page's knobs), "" for any
    not set. The starting shape of the form."""
    keys = keys if keys is not None else KNOWN_KNOBS
    return {k: get(text, k) for k, _ in keys}


def validate(text: str) -> None:
    """Refuse the file the way `scripts/env.sh` does: a comment that trails a
    value on the same line. Raises ToolError naming the key."""
    for line in text.split("\n"):
        if not line or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m and _TRAILING_COMMENT.search(m.group("value")):
            raise ToolError(
                f"env file: '{m.group('key')}' has a comment after its value; put the comment on its own line"
            )


def save_env_file(path, values: dict[str, str], *, unknown_note: str = "") -> str:
    """Write the file back, losslessly.

    Every key the file already carries is rewritten in place with its new value
    from `values` (its old value when `values` does not name it — unknown keys
    survive a save untouched). The keys' order, and every comment and blank line,
    is kept exactly. A key that is in `values` but not yet in the file is appended
    at the end. A key whose value is a comment-free assignment never gains a
    trailing comment of its own (env.sh would refuse one).

    Raises ToolError (without touching the file) when any value would carry a
    trailing comment. Returns the written text."""
    validate("\n".join(f"{k}={v}" for k, v in values.items() if v))

    lines = parse_lines(read_env_file(path))
    # index the file's assigns so we can rewrite values in place
    assigns = []  # (index, key)
    for i, (kind, payload) in enumerate(lines):
        if kind == "assign":
            m = _LINE.match(payload)
            if m:
                assigns.append((i, m.group("key")))
    present = {k for _, k in assigns}

    # rewrite each existing assign with its new value (or leave it untouched)
    for i, k in assigns:
        if k in values:
            lines[i] = ("assign", f"{k}={values[k]}")

    # append any key in `values` that is not yet in the file
    for k in values:
        if k not in present:
            lines.append(("assign", f"{k}={values[k]}"))

    from pathlib import Path

    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = _render(lines)
    validate(text)  # never write a file env.sh would refuse
    p.write_text(text)
    return text


def example_to_values(example_text: str, known: list[tuple[str, str]] | None = None) -> dict[str, str]:
    """The current values of the page's knobs read from a gojira.env.example —
    the starting point of a fresh file before the operator edits it."""
    known = known or KNOWN_KNOBS
    out = {}
    for k, _ in known:
        out[k] = get(example_text, k)
    return out


def example_docs(example_text: str) -> dict[str, str]:
    """The one-line doc for each knob, from the example file the profile's
    Gojira checkout ships (the source of truth, which changes with Gojira's
    releases). A key with no comment line above it gets ""."""
    docs: dict[str, str] = {}
    lines = example_text.split("\n")
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if not m:
            continue
        key = m.group("key")
        doc = ""
        # the last comment line of the block sitting directly above the key.
        # Gojira wraps a long comment over several lines; its final line is the
        # one the key's value follows, so it reads as the key's own one-liner.
        # An all-dashes section banner carries no doc, so the key gets none.
        block = []
        j = i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            block.append(lines[j].strip().lstrip("#").strip())
            j -= 1
        if block:  # block[0] is the line closest to the key
            if not set(block[0]) <= {"-", " "}:
                doc = block[0]
        docs[key] = doc
    return docs


__all__ = [
    "KNOWN_KNOBS", "KNOWN_KEYS", "read_env_file", "parse_lines", "get", "get_all", "validate",
    "save_env_file", "example_to_values", "example_docs",
]