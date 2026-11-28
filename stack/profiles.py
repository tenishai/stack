"""Profiles: one install each, as the UI sees them (stack#12).

A profile is a name and pointers. The stack file stays the truth about the
install; the profile adds what the shell used to carry and strangers got
wrong — which kubeconfig, which cloud account, where the repo clones and the
`cbl` binary are — so every tool call is made with those pinned and nothing
depends on an export. The list lives in one small YAML file of the
operator's own (`~/.config/stack/profiles.yaml`, or $STACK_PROFILES).
Nothing in it is secret: paths, never contents.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from stack.runs import Target

DEFAULT_PATH = Path("~/.config/stack/profiles.yaml")


def profiles_path() -> Path:
    return Path(os.environ.get("STACK_PROFILES") or DEFAULT_PATH).expanduser()


class Profile(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    stack_file: str
    kubeconfig: str = ""      # KUBECONFIG for every kubectl call; empty = the operator's default
    cloud_account: str = ""   # CLOUDSDK_CORE_ACCOUNT for gcloud-backed calls (kubectl's auth plugin)
    repos: str = ""           # clones of the apps' upstream repos (--repos); empty = the stack file's dir
    cbl: str = ""             # the cabal CLI, for a stack that bootstraps cabal (--cbl)
    env_file: str = ""        # the install's gojira.env; recorded now, used by the install flow (#12 increment 2)
    gojira_repo: str = ""     # the profile's Gojira checkout (Install Gojira runs its scripts/install.sh); empty = the repos dir's gojira clone
    # The four things the install is told (stack.site): they fill the stack
    # file's tokens and write the env file. Empty = the stack file carries none.
    org: str = ""             # the GitHub organization the deploy repos, kobo's config repo and the agents live in
    gcp_project: str = ""     # the GCP project (the cluster, the registry, the identities)
    region: str = ""          # where the cluster is (us-central1)
    cluster: str = ""         # the GKE cluster's name, also the name it is registered under in Gojira

    @model_validator(mode="after")
    def _paths(self):
        if not self.stack_file.strip():
            raise ValueError("stack_file is required")
        return self

    def env(self) -> dict[str, str]:
        """What is pinned on every tool call this profile makes."""
        out = {}
        if self.kubeconfig:
            out["KUBECONFIG"] = str(Path(self.kubeconfig).expanduser())
        if self.cloud_account:
            out["CLOUDSDK_CORE_ACCOUNT"] = self.cloud_account
        return out

    def values(self) -> dict[str, str]:
        """The four, by token name, only those set."""
        pairs = (("org", self.org), ("project", self.gcp_project), ("region", self.region), ("cluster", self.cluster))
        return {k: v.strip() for k, v in pairs if v.strip()}

    def values_complete(self) -> bool:
        return len(self.values()) == 4

    def target(self) -> Target:
        return Target(
            stack_file=Path(self.stack_file).expanduser(),
            repos=Path(self.repos).expanduser() if self.repos else None,
            cbl=str(Path(self.cbl).expanduser()) if self.cbl else "",
            env=self.env(),
            values=self.values(),
            env_file=Path(self.env_file).expanduser() if self.env_file else None,
        )


class Profiles(BaseModel):
    profiles: list[Profile] = []

    def get(self, name: str) -> Profile | None:
        return next((p for p in self.profiles if p.name == name), None)

    def put(self, profile: Profile) -> None:
        """Add or replace by name; the order is the order of first appearance."""
        for i, p in enumerate(self.profiles):
            if p.name == profile.name:
                self.profiles[i] = profile
                return
        self.profiles.append(profile)

    def remove(self, name: str) -> bool:
        before = len(self.profiles)
        self.profiles = [p for p in self.profiles if p.name != name]
        return len(self.profiles) < before

    @classmethod
    def load(cls, path: Path) -> Profiles:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"profiles": [p.model_dump(exclude_defaults=True) for p in self.profiles]}
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
