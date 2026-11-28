"""The four things an install is told, and what follows from them (stack#12).

A stranger does not copy the stack file and edit it. The file carries four
tokens — `{org}`, `{project}`, `{region}` and `{cluster}` — and the profile
fills them when the file is read; the same four fill a `template:` file when
its Secret is written. And the env file Gojira installs from is written, not
typed: project, region and cluster are the profile's; the roles Gojira may
grant are the union of what the stack file's apps ask for; the cluster each
environment name means is the one cluster this install has. Nothing here is
secret, and nothing here touches a cluster.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from stack.clients import ToolError
from stack.model import Stack

TOKENS = ("org", "project", "region", "cluster")
# Tokens a `template:` file may also carry; the installer fills them when it
# writes the Secret (the App's id and installation id come from the env file
# and GitHub, the token from Gojira). A stack file never carries them.
LATE_TOKENS = ("gojira_token", "app_id", "installation_id")
_TOKEN = re.compile(r"\{(" + "|".join(TOKENS + LATE_TOKENS) + r")\}")


def fill(text: str, values: dict[str, str], *, allow: tuple[str, ...] = LATE_TOKENS) -> str:
    """`text` with every `{token}` replaced from `values`. A token with no value
    is an error naming it — unless it is in `allow`, which stays as written for
    a later step to fill."""
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if values.get(name):
            return values[name]
        if name in allow:
            return m.group(0)
        if name not in missing:
            missing.append(name)
        return m.group(0)

    out = _TOKEN.sub(sub, text)
    if missing:
        order = TOKENS + LATE_TOKENS
        missing.sort(key=order.index)
        raise ValueError("needs values for " + ", ".join("{" + m + "}" for m in missing)
                         + ": set org, project, region and cluster on the profile, or pass --set <name>=<value>")
    return out


def tokens_in(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


def env_values(stack: Stack, values: dict[str, str], github, repo_root: Path | None = None) -> dict[str, str]:
    """The knobs of gojira.env that follow from the four values and the stack
    file, for an install that makes its own cluster with terraform:

    - PROJECT, REGION, CLUSTER: the profile's.
    - GOJIRA_GRANTABLE_ROLES: every `identity.gcp_roles` an app will ask for,
      read the way the installer writes each deploy repo's contract: the
      upstream's gojira.yaml with the stack file's `contract:` over it.
    - GOJIRA_GRANTABLE_CLUSTER_ROLES: every `cluster_roles` the same way.
    - GOJIRA_ENVIRONMENT_CLUSTERS: each environment the apps name -> this
      install's one cluster.

    The upstream contract is read from a clone under `repo_root` when there is
    one, else from GitHub (a stranger clones nothing)."""
    roles: set[str] = set()
    cluster_roles: set[str] = set()
    for app in stack.apps:
        text = ""
        local = (repo_root / app.repo.split("/")[1] / "gojira.yaml") if repo_root else None
        if local is not None and local.is_file():
            text = local.read_text()
        else:
            try:
                text = github.read_file(app.repo, "gojira.yaml")
            except ToolError:
                text = ""
        doc = (yaml.safe_load(text) or {}) if text else {}
        if not isinstance(doc, dict):
            doc = {}
        identity = {**(doc.get("identity") or {}), **(app.contract.get("identity") or {})}
        roles.update(identity.get("gcp_roles") or [])
        cluster_roles.update(app.contract.get("cluster_roles", doc.get("cluster_roles")) or [])
    envs: dict[str, str] = {}
    for app in stack.apps:
        envs.setdefault(app.environment, stack.cluster.name)
    return {
        "PROJECT": values["project"],
        "REGION": values["region"],
        "CLUSTER": values["cluster"],
        "GOJIRA_TERRAFORM": "1",
        "GOJIRA_ENVIRONMENT_CLUSTERS": "{" + ", ".join(f"{k}: {v}" for k, v in envs.items()) + "}",
        "GOJIRA_GRANTABLE_ROLES": ",".join(sorted(roles)),
        "GOJIRA_GRANTABLE_CLUSTER_ROLES": ",".join(sorted(cluster_roles)),
    }


DERIVED_KEYS = ("PROJECT", "REGION", "CLUSTER", "GOJIRA_TERRAFORM", "GOJIRA_ENVIRONMENT_CLUSTERS",
                "GOJIRA_GRANTABLE_ROLES", "GOJIRA_GRANTABLE_CLUSTER_ROLES")


def write_env(path: str | Path, values: dict[str, str], example_text: str = "") -> str:
    """Write the derived knobs into the install's env file, losslessly over
    whatever is there (the App id and pem path the card wrote, a knob the
    operator set by hand, every comment). A file that does not exist yet starts
    from Gojira's gojira.env.example, so each knob keeps the doc above it."""
    from stack import envfile

    p = Path(path).expanduser()
    if not p.exists() and example_text:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(example_text)
    return envfile.save_env_file(p, values)


__all__ = ["TOKENS", "LATE_TOKENS", "DERIVED_KEYS", "fill", "tokens_in", "env_values", "write_env"]
