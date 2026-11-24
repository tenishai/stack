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

# env.sh's skip is `case ''|'#'*`: only an empty line or a `#` in column 0 is
# skipped. Anything else with a `#` somewhere (whitespace-then-`#`, or a
# mid-line `#`) falls through and is refused. Mirror that exactly: a
# column-0 `#` is a comment; whitespace-then-`#` (not a column-0 `#`) is the
# shape env.sh refuses (a KEY=value carrying it, or a line that is one).
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
        elif line.startswith("#"):  # column 0 only: env.sh skips `''|'#'*`
            out.append(("comment", line))
        elif _LINE.match(line):
            out.append(("assign", line))
        else:
            out.append(("other", line))
    return out


def _render(lines: list[tuple[str, str]], append: list[str] | None = None) -> str:
    # A source file ends in a newline, so its text splits into a final empty
    # "blank". Drop that artifact here, at render time (not before we append):
    # the lines in `append` pass under the content, not under the blank, and a
    # round-trip that appends nothing is byte-identical.
    if lines and lines[-1] == ("blank", ""):
        lines = lines[:-1]
    if append:
        lines = lines + [("assign", line) for line in append]
    text = "\n".join(p for _, p in lines)
    # keep a single trailing newline: a real env file ends in one; an empty file
    # is the empty string, not a lone newline.
    return text + "\n" if text else ""


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
    """Refuse the file the way `scripts/env.sh` does. env.sh skips only an empty
    line or a `#` in column 0; any other line carrying a `#` (a `KEY=1   # why`,
    or an indented `   # note` that is not a column-0 comment) is refused. Raises
    ToolError naming the key it can, or the line when it cannot."""
    for line in text.split("\n"):
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            # env.sh refuses a value that carries whitespace-then-`#` (the `KEY=1 # why`
            # shape; a `#` directly on a non-space char is still the value, not a comment)
            if re.search(r"[\t ]#", m.group("value")):
                raise ToolError(
                    f"env file: '{m.group('key')}' has a comment after its value; put the comment on its own line"
                )
            continue
        # not a KEY=value at all, yet it carries a `#` (e.g. an indented `   # note`):
        # a comment env.sh will refuse
        if "#" in line:
            raise ToolError(f"env file: a comment is not on its own line: '{line.strip()}'")


def save_env_file(path, values: dict[str, str], *, unknown_note: str = "") -> str:
    """Write the file back, losslessly.

    Every key the file already carries is rewritten in place with its value from
    `values` — even an empty one: `KEY=` is a knob the operator cleared, and env.sh
    reads it as unset. A key in `values` that is not yet in the file is appended
    only if it has a value (never a wall of empty knobs). The keys' order, and
    every comment and blank line, is kept exactly.

    Raises ToolError (without touching the file) when the file, or any value,
    carries the comment shape env.sh would refuse. Returns the written text."""
    validate(read_env_file(path))      # refuse before writing: the file itself
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

    # rewrite each existing assign with its form value (even "" = cleared)
    for i, k in assigns:
        if k in values:
            lines[i] = ("assign", f"{k}={values[k]}")

    # collect the keys to append (in `values` order); the writer adds them at
    # render time, under the content, not under the split's trailing blank
    append = [f"{k}={v}" for k, v in values.items() if k not in present and v]

    from pathlib import Path

    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = _render(lines, append)
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
    """The doc for each knob, from the example file the profile's Gojira checkout
    ships (the source of truth, which changes with Gojira's releases).

    The example leads a key — or a *pair* of keys — with a paragraph: the
    comments sitting directly above the first key of the group, in reading order
    (Gojira wraps a long comment over several lines; it documents a pair in one
    paragraph, e.g. the GitHub App's id and its pem). A section banner (a comment
    starting `---`) is a divider, not a doc, so a key under a banner only gets
    "". A key with no comment directly above it (its peer sits there) inherits the
    doc of the key immediately above it — that is how the second of a documented
    pair reaches the words that explain them. CSS clamps the length; the text is
    never truncated here."""
    lines = example_text.split("\n")
    # each key with the comment block sitting directly above it, in reading order
    keys = []
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if not m:
            continue
        block = []
        j = i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            block.append(lines[j].strip().lstrip("#").strip())
            j -= 1
        keys.append({"key": m.group("key"), "block": list(reversed(block))})

    def clean(block: list[str]) -> str:
        # the real doc lines, in reading order, joined into one sentence; a `---`
        # divider or a blank opens a new paragraph (a line break in the output).
        paragraphs: list[list[str]] = [[]]
        for t in block:
            if t.startswith("---") or t == "":
                paragraphs.append([])
            else:
                paragraphs[-1].append(t)
        while paragraphs and not paragraphs[0]:
            paragraphs.pop(0)
        while paragraphs and not paragraphs[-1]:
            paragraphs.pop()
        return "\n".join(" ".join(p) for p in paragraphs)

    docs: dict[str, str] = {}
    prev = ""
    for d in keys:
        own = clean(d["block"])
        # a key with no doc of its own inherits the key immediately above it: the
        # second of a pair takes the paragraph that documents both. A key under a
        # banner only, above a key whose own block is also banner-only, gets "".
        docs[d["key"]] = own if own else prev
        prev = docs[d["key"]]
    return docs


__all__ = [
    "KNOWN_KNOBS", "KNOWN_KEYS", "read_env_file", "parse_lines", "get", "get_all", "validate",
    "save_env_file", "example_to_values", "example_docs",
]