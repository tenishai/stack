"""The stack file: what an install of the tenish stack consists of.

One YAML file describes everything a person did by hand to bring the stack up
on a Gojira (2026-10-08, ~25 touches across two people): the apps in the
order they must come up, the cluster objects an operator applies once, the
ConfigMaps, and the Secrets, each value named by where it comes from rather
than written down. The installer reads this and does the touches; the person
keeps the merges.

Value sources are the whole vocabulary for a Secret or ConfigMap value:

    file:<path>                  the bytes of a file on the operator's machine
    generate:<n>                 n random bytes, base64, made once and kept in a
                                 600 file next to the stack file so a rerun reuses it
    gojira-token                 the install's own API token (kobo needs it)
    template:<path>              a file with {gojira_token} filled in
    cabal-token:<sub>:<scope>    a long-lived service JWT minted on the cabal this
                                 stack just brought up (liminal, kobo)
    literal:<text>               the text itself; for names, never for secrets

Nothing here is a secret: the file names where values live, not what they are.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SOURCE = re.compile(r"^(file|generate|template|cabal-token|literal):(.+)$|^gojira-token$")


class Source(BaseModel):
    """One value, by origin. Parsed from the `kind:arg` string in the file."""

    kind: Literal["file", "generate", "gojira-token", "template", "cabal-token", "literal"]
    arg: str = ""

    @classmethod
    def parse(cls, text: str) -> Source:
        m = SOURCE.match(text)
        if not m:
            raise ValueError(
                f"{text!r} is not a value source (file:, generate:, gojira-token, "
                "template:, cabal-token:<sub>:<scope>, literal:)"
            )
        if text == "gojira-token":
            return cls(kind="gojira-token")
        kind, arg = m.group(1), m.group(2)
        if kind == "generate" and not re.fullmatch(r"\d+(:[a-z0-9-]+)?", arg):
            raise ValueError(f"generate: takes a byte count, optionally :<name> to share the value, got {arg!r}")
        if kind == "cabal-token" and ":" not in arg:
            raise ValueError(f"cabal-token takes <sub>:<scope> (e.g. service:kobo:operator), got {arg!r}")
        return cls(kind=kind, arg=arg)

    @property
    def path(self) -> Path:
        """`~` expanded; a relative path is resolved against the stack file's
        directory by the installer, so a stack file can travel with its files."""
        return Path(self.arg).expanduser()

    # generate:<n>[:<name>] — a named value is shared by every source that
    # names it (litellm and cabal must hold the same LITELLM_MASTER_KEY).
    @property
    def gen_bytes(self) -> int:
        return int(self.arg.split(":", 1)[0])

    @property
    def gen_name(self) -> str:
        return self.arg.split(":", 1)[1] if ":" in self.arg else ""

    # cabal-token:<sub>:<scope> — the sub itself holds a colon (service:kobo),
    # so the scope is the last segment.
    @property
    def cabal_sub(self) -> str:
        return self.arg.rsplit(":", 1)[0]

    @property
    def cabal_scope(self) -> str:
        return self.arg.rsplit(":", 1)[1]


class App(BaseModel):
    """One app of the stack, in the order it must come up."""

    name: str = Field(pattern=DNS_LABEL.pattern)
    repo: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    workload_class: str = "gateway"
    namespace: str = ""  # default: the app's name
    environment: str = "dev"
    # Cluster-scoped objects an operator applies once, relative to the repo
    # root (a ClusterRole Gojira then binds through `cluster_roles`).
    cluster_objects: list[str] = []
    # ConfigMaps in the app's namespace: name -> key -> source.
    configmaps: dict[str, dict[str, Source]] = {}
    # Secrets in the app's namespace: name -> key -> source.
    secrets: dict[str, dict[str, Source]] = {}
    # Secrets whose values come from this app once it is up (cabal mints
    # tokens): resolved after the app is running, not before.
    bootstrap: Literal["", "cabal"] = ""
    # A first install of a repo the operator owns may let the installer
    # merge the setup PR. Off by default: the PR is the review.
    auto_merge_setup_pr: bool = False
    # Deploy a published image from a repo of the operator's own (gojira#108):
    # `repo` stays the upstream, whose k8s/base and cluster objects are read
    # from the clone under --repos; `deploy_repo` (org/name) is created if
    # missing, given a gojira.yaml naming `image`, and connected in its place.
    # `contract` keys are merged last into that gojira.yaml (env, identity,
    # ...): the install's facts, which the upstream's contract cannot know.
    image: str = ""
    deploy_repo: str = Field(default="", pattern=r"^$|^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    contract: dict = {}
    # Where this app's release images are published (stack#12): a repo ref with
    # no tag (ghcr.io/example/app). `stack upgrade` copies `<source_image>:<tag
    # of image:>` into the install registry when the digest isn't there yet.
    source_image: str = ""

    @field_validator("configmaps", "secrets", mode="before")
    @classmethod
    def _parse_sources(cls, v):
        out = {}
        for obj, keys in (v or {}).items():
            out[obj] = {k: Source.parse(s) if isinstance(s, str) else s for k, s in (keys or {}).items()}
        return out

    @model_validator(mode="after")
    def _defaults(self):
        if not self.namespace:
            self.namespace = self.name
        if self.deploy_repo and not self.image:
            raise ValueError(f"{self.name}: deploy_repo needs image: (the published image the deploy repo names)")
        if self.contract and not self.deploy_repo:
            raise ValueError(f"{self.name}: contract: is for a deploy_repo the installer writes; edit {self.repo}'s gojira.yaml instead")
        if self.source_image and not self.deploy_repo:
            raise ValueError(f"{self.name}: source_image: is for an image app (set image: and deploy_repo:)")
        if self.source_image and ("@" in self.source_image or ":" in self.source_image.split("/")[-1]):
            raise ValueError(f"{self.name}: source_image: is a repo ref with no tag or digest; "
                             "the tag comes from image:")
        return self

    @property
    def connected_repo(self) -> str:
        """The repo Gojira connects: the deploy repo when there is one."""
        return self.deploy_repo or self.repo

    def needs_cabal(self) -> bool:
        return any(s.kind == "cabal-token" for keys in self.secrets.values() for s in keys.values())


class Cluster(BaseModel):
    name: str
    gcp_project: str
    location: str
    gke_cluster: str = ""  # default: the registered name
    ar_repo: str = ""      # default: <location>-docker.pkg.dev/<project>/apps

    @model_validator(mode="after")
    def _defaults(self):
        if not self.gke_cluster:
            self.gke_cluster = self.name
        if not self.ar_repo:
            self.ar_repo = f"{self.location}-docker.pkg.dev/{self.gcp_project}/apps"
        return self


class Gojira(BaseModel):
    """How to reach the Gojira this stack installs onto."""

    namespace: str = "gojira"
    # A URL the installer can reach directly; empty means port-forward the
    # Service in `namespace` on the current kubectl context.
    url: str = ""
    kube_context: str = ""


class Stack(BaseModel):
    gojira: Gojira = Gojira()
    cluster: Cluster
    apps: list[App] = Field(min_length=1)
    # How `stack upgrade` lands a pin on a deploy repo: a PR someone merges
    # (the review), or a direct push gated by the operator's own confirm.
    upgrade_gate: Literal["pr", "push"] = "pr"

    @model_validator(mode="after")
    def _order(self):
        names = [a.name for a in self.apps]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate app names: {names}")
        cabal_at = next((i for i, a in enumerate(self.apps) if a.bootstrap == "cabal"), None)
        for i, a in enumerate(self.apps):
            if a.needs_cabal() and (cabal_at is None or cabal_at > i):
                raise ValueError(
                    f"app {a.name!r} needs a cabal-token but no app with `bootstrap: cabal` "
                    "comes before it in the list"
                )
        return self

    @classmethod
    def load(cls, path: str | Path) -> Stack:
        import yaml

        return cls.model_validate(yaml.safe_load(Path(path).read_text()) or {})
