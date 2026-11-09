"""The four hands the installer uses: Gojira's API, kubectl, gh and gcloud.

Each is a small class over the real tool with the handful of calls the steps
need, and a Protocol so tests run the steps against fakes (tests/fakes.py).
Nothing here decides anything; `steps.py` does.

Secret values pass through `KubectlClient.apply_secret` and are never logged,
echoed into an exception, or kept after the call.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

log = logging.getLogger("stack")


class ToolError(RuntimeError):
    """A tool said no. The message is the tool's own sentence, trimmed."""


def tool_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment every tool runs in: the operator's own, minus a
    PYTHONPATH (someone else's site-packages), plus what the caller pins —
    `KUBECONFIG` and `CLOUDSDK_CORE_ACCOUNT` from a profile (`stack ui`), so
    no shell export is relied on and no context accident can happen."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(extra or {})
    return env


def _run(args: list[str], *, stdin: str | None = None, check: bool = True, timeout: int = 120,
         env: dict[str, str] | None = None) -> str:
    try:
        proc = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout,
                              env=env or tool_env())
    except subprocess.TimeoutExpired:
        raise ToolError(f"{' '.join(args[:3])} did not answer in {timeout}s") from None
    if check and proc.returncode != 0:
        raise ToolError(f"{' '.join(args[:3])} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


# --- Gojira -----------------------------------------------------------------


@dataclass
class AppRow:
    id: int
    name: str
    status: str
    setup_pr_url: str = ""


class GojiraClient(Protocol):
    def clusters(self) -> list[dict]: ...
    def register_cluster(self, name: str, gcp_project: str, location: str, gke_cluster: str, ar_repo: str) -> None: ...
    def apps(self) -> list[AppRow]: ...
    def app(self, app_id: int) -> AppRow: ...
    def connect(self, name: str, repo: str, workload_class: str, environment: str, namespace: str,
                auto_merge: bool) -> AppRow: ...
    def provision(self, app_id: int) -> None: ...
    def sync(self, app_id: int) -> AppRow: ...
    def api_token(self) -> str: ...


class HttpGojira:
    """Gojira over HTTP, with the bearer token read from its own Secret.

    The URL is either given (a front door) or a port-forward the installer
    opens on the Service in Gojira's namespace. The token is read once from
    `gojira-github-app/api_token`, the way `make install` prints it.
    """

    def __init__(self, kubectl: KubectlClient, url: str = "", namespace: str = "gojira",
                 port: int = 18097):
        self._kubectl = kubectl
        self._ns = namespace
        self._pf: subprocess.Popen | None = None
        if not url:
            self._pf = kubectl.port_forward(namespace, "svc/gojira", port, 80)
            url = f"http://127.0.0.1:{port}"
        self.url = url.rstrip("/")
        self._token = kubectl.secret_value(namespace, "gojira-github-app", "api_token")
        self._http = httpx.Client(base_url=self.url, timeout=600,
                                  headers={"Authorization": f"Bearer {self._token}"})
        for _ in range(30):
            try:
                if self._http.get("/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise ToolError(f"Gojira at {self.url} did not answer /healthz")

    def close(self) -> None:
        if self._pf:
            self._pf.terminate()

    def api_token(self) -> str:
        return self._token

    def clusters(self) -> list[dict]:
        return self._http.get("/api/v1/clusters").json()["clusters"]

    def register_cluster(self, name, gcp_project, location, gke_cluster, ar_repo) -> None:
        # Registration is the UI's form today (no JSON route yet); it answers 303.
        r = self._http.post("/clusters", data={
            "name": name, "gcp_project": gcp_project, "location": location,
            "gke_cluster": gke_cluster, "ar_repo": ar_repo,
        }, follow_redirects=False)
        if r.status_code not in (200, 303):
            raise ToolError(f"register cluster {name}: {r.status_code} {r.text[:200]}")

    @staticmethod
    def _row(a: dict) -> AppRow:
        return AppRow(id=a["id"], name=a["name"], status=a["status"], setup_pr_url=a.get("setup_pr_url") or "")

    def apps(self) -> list[AppRow]:
        return [self._row(a) for a in self._http.get("/api/v1/apps").json()["apps"]]

    def app(self, app_id: int) -> AppRow:
        return self._row(self._http.get(f"/api/v1/apps/{app_id}").json())

    def connect(self, name, repo, workload_class, environment, namespace, auto_merge) -> AppRow:
        r = self._http.post("/api/v1/apps", json={
            "name": name, "repo_full_name": repo, "workload_class": workload_class,
            "environments": [{"name": environment, "namespace": namespace}],
            "auto_merge_setup_pr": auto_merge,
        })
        if r.status_code >= 400:
            raise ToolError(f"connect {name}: {r.status_code} {r.json().get('error', r.text[:200])}")
        return self.app(r.json()["id"])

    def provision(self, app_id: int) -> None:
        r = self._http.post(f"/api/v1/apps/{app_id}/provision")
        if r.status_code >= 400:
            raise ToolError(f"provision {app_id}: {r.status_code} {r.text[:200]}")

    def sync(self, app_id: int) -> AppRow:
        r = self._http.post(f"/api/v1/apps/{app_id}/sync")
        if r.status_code >= 400:
            raise ToolError(f"sync {app_id}: {r.status_code} {r.text[:200]}")
        return self.app(app_id)


# --- kubectl ----------------------------------------------------------------


class KubectlClient(Protocol):
    def current_context(self) -> str: ...
    def namespace_exists(self, ns: str) -> bool: ...
    def secret_exists(self, ns: str, name: str) -> bool: ...
    def secret_value(self, ns: str, name: str, key: str) -> str: ...
    def apply_secret(self, ns: str, name: str, data: dict[str, str]) -> None: ...
    def configmap_exists(self, ns: str, name: str) -> bool: ...
    def apply_configmap(self, ns: str, name: str, data: dict[str, str]) -> None: ...
    def apply_file(self, path: str) -> str: ...
    def pods_ready(self, ns: str) -> tuple[int, int]: ...
    def logs_grep(self, ns: str, deploy: str, pattern: str) -> str | None: ...
    def deployed_image(self, ns: str, deploy: str) -> str: ...
    def restart(self, ns: str, deploy: str) -> None: ...
    def port_forward(self, ns: str, target: str, local: int, remote: int) -> subprocess.Popen: ...


class KubectlCLI:
    def __init__(self, context: str = "", env: dict[str, str] | None = None):
        self._ctx = ["--context", context] if context else []
        self._env = tool_env(env)
        if not shutil.which("kubectl"):
            raise ToolError("kubectl is not on PATH")
        # Every call answers or fails within seconds: a context whose cluster is
        # gone, unreachable or wants an interactive login must not hang the
        # installer (a stranger's first run sat in a secret read until ^C,
        # 2026-10-14). Not on port-forward, which is meant to live.
        self._base = ["kubectl", *self._ctx, "--request-timeout=20s"]

    def _k(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        return _run([*self._base, *args], stdin=stdin, check=check, timeout=60, env=self._env)

    def _exists(self, ns: str, kind: str, name: str) -> bool:
        return subprocess.run([*self._base, "-n", ns, "get", kind, name], capture_output=True, timeout=60,
                              env=self._env).returncode == 0

    def current_context(self) -> str:
        """The context calls go to: the configured one, else kubectl's current."""
        if self._ctx:
            return self._ctx[1]
        return _run(["kubectl", "config", "current-context"], check=False, timeout=30,
                    env=self._env).strip() or "(none)"

    def namespace_exists(self, ns: str) -> bool:
        return subprocess.run([*self._base, "get", "ns", ns], capture_output=True, timeout=60,
                              env=self._env).returncode == 0

    def secret_exists(self, ns, name) -> bool:
        return self._exists(ns, "secret", name)

    def secret_value(self, ns, name, key) -> str:
        out = self._k("-n", ns, "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}")
        return base64.b64decode(out).decode()

    def apply_secret(self, ns, name, data) -> None:
        # Built as a manifest on stdin: values never appear on a command line.
        doc = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
               "metadata": {"name": name, "namespace": ns},
               "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}
        self._k("apply", "-f", "-", stdin=json.dumps(doc))

    def configmap_exists(self, ns, name) -> bool:
        return self._exists(ns, "configmap", name)

    def apply_configmap(self, ns, name, data) -> None:
        doc = {"apiVersion": "v1", "kind": "ConfigMap",
               "metadata": {"name": name, "namespace": ns}, "data": data}
        self._k("apply", "-f", "-", stdin=json.dumps(doc))

    def apply_file(self, path) -> str:
        return self._k("apply", "-f", path).strip()

    def pods_ready(self, ns) -> tuple[int, int]:
        out = self._k("-n", ns, "get", "pods", "-o",
                      "jsonpath={range .items[*]}{.status.containerStatuses[*].ready}{\"\\n\"}{end}",
                      check=False)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return sum(1 for ln in lines if "false" not in ln), len(lines)

    def logs_grep(self, ns, deploy, pattern, since: str = "10m") -> str | None:
        out = self._k("-n", ns, "logs", f"deploy/{deploy}", f"--since={since}", check=False)
        m = re.search(pattern, out)
        return m.group(1) if m else None

    def deployed_image(self, ns, deploy) -> str:
        return self._k("-n", ns, "get", "deploy", deploy, "-o",
                       "jsonpath={.spec.template.spec.containers[0].image}", check=False).strip()

    def restart(self, ns, deploy) -> None:
        self._k("-n", ns, "rollout", "restart", f"deploy/{deploy}")
        self._k("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")

    def port_forward(self, ns, target, local, remote) -> subprocess.Popen:
        return subprocess.Popen(["kubectl", *self._ctx, "-n", ns, "port-forward", target, f"{local}:{remote}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self._env)


# --- gh ---------------------------------------------------------------------


class GitHubClient(Protocol):
    def repo_exists(self, repo: str) -> bool: ...
    def create_repo(self, repo: str) -> None: ...
    def push_files(self, repo: str, files: dict[str, str], message: str) -> int: ...
    def read_file(self, repo: str, path: str) -> str: ...
    def open_pr(self, repo: str, files: dict[str, str], title: str, body: str, branch: str) -> str: ...
    def pr_state(self, pr_url: str) -> str: ...
    def merge_pr(self, pr_url: str) -> None: ...
    def dispatch(self, repo: str, workflow: str) -> None: ...
    def last_run(self, repo: str, workflow: str) -> tuple[str, str]: ...
    def login(self) -> str: ...
    def org_access(self, org: str) -> str: ...


class GhCLI:
    def __init__(self, env: dict[str, str] | None = None):
        self._env = tool_env(env)
        if not shutil.which("gh"):
            raise ToolError("gh is not on PATH")

    def _gh(self, *args: str, check: bool = True, timeout: int = 120) -> str:
        return _run(["gh", *args], check=check, timeout=timeout, env=self._env)

    def login(self) -> str:
        """The account gh is logged in as, or a ToolError saying it isn't."""
        out = self._gh("api", "user", "--jq", ".login", check=False).strip()
        if not out:
            raise ToolError("gh is not logged in (gh auth login)")
        return out

    def org_access(self, org: str) -> str:
        """Why gh cannot see `org`'s repos, or "" when it can. SAML SSO never
        says 401: a token without the org's SSO grant lists none of its repos
        and `orgs/<org>` answers 403 naming SAML (gotcha, 2026-10-22)."""
        proc = subprocess.run(["gh", "api", f"orgs/{org}"], capture_output=True, text=True,
                              env=self._env, timeout=60)
        if proc.returncode == 0:
            return ""
        if "SAML" in proc.stderr or "SSO" in proc.stderr:
            return (f"your gh token has no SSO grant for {org}: on github.com, the token's "
                    "Configure SSO -> Authorize, then `gh auth login --with-token`")
        if "404" in proc.stderr:
            user = subprocess.run(["gh", "api", f"users/{org}"], capture_output=True, text=True,
                                  env=self._env, timeout=60)
            return "" if user.returncode == 0 else f"gh finds no org or user named {org}"
        return f"gh cannot read org {org}: {proc.stderr.strip()[:200]}"

    @staticmethod
    def _split(pr_url: str) -> tuple[str, str]:
        m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)$", pr_url)
        if not m:
            raise ToolError(f"not a pull request URL: {pr_url}")
        return m.group(1), m.group(2)

    def repo_exists(self, repo) -> bool:
        return subprocess.run(["gh", "repo", "view", repo, "--json", "name"], capture_output=True,
                              env=self._env).returncode == 0

    def create_repo(self, repo) -> None:
        self._gh("repo", "create", repo, "--private",
                 "--description", "Deploy repo for a published image, managed by the stack installer (Gojira image:)")

    def push_files(self, repo, files, message) -> int:
        """Write `files` onto the repo's default branch and push; the number of
        files that changed (0 means nothing was pushed). Direct to the branch:
        this is a repo the installer made for exactly this content."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._gh("repo", "clone", repo, tmp, "--", "--quiet")
            git = ["git", "-C", tmp]
            if subprocess.run([*git, "rev-parse", "--verify", "HEAD"], capture_output=True,
                              env=self._env).returncode != 0:
                _run([*git, "symbolic-ref", "HEAD", "refs/heads/main"], env=self._env)      # an empty repo: start main
            changed = 0
            for rel, content in files.items():
                target = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target) and open(target).read() == content:
                    continue
                with open(target, "w") as fh:
                    fh.write(content)
                _run([*git, "add", "--", rel], env=self._env)
                changed += 1
            if changed:
                _run([*git, "commit", "-q", "-s", "-m", message], env=self._env)
                _run([*git, "push", "-q", "-u", "origin", "HEAD"], env=self._env)
            return changed

    def read_file(self, repo, path) -> str:
        out = self._gh("api", f"repos/{repo}/contents/{path}", "--jq", ".content")
        return base64.b64decode(out).decode()

    def open_pr(self, repo, files, title, body, branch) -> str:
        """A PR carrying exactly `files` on `branch`, opened or found. A rerun
        finds the open PR and answers its URL; the branch is force-pushed so
        the PR always shows the latest content."""
        import tempfile
        existing = json.loads(self._gh("pr", "list", "-R", repo, "--head", branch,
                                       "--state", "open", "--json", "url"))
        with tempfile.TemporaryDirectory() as tmp:
            self._gh("repo", "clone", repo, tmp, "--", "--quiet")
            git = ["git", "-C", tmp]
            _run([*git, "checkout", "-q", "-B", branch], env=self._env)
            for rel, content in files.items():
                target = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w") as fh:
                    fh.write(content)
                _run([*git, "add", "--", rel], env=self._env)
            _run([*git, "commit", "-q", "-s", "-m", title], env=self._env)
            _run([*git, "push", "-q", "-f", "-u", "origin", branch], env=self._env)
            if existing:
                return existing[0]["url"]
            out = self._gh("pr", "create", "-R", repo, "--head", branch,
                           "--title", title, "--body", body)
            return out.strip().splitlines()[-1]

    def pr_state(self, pr_url) -> str:
        repo, num = self._split(pr_url)
        out = self._gh("pr", "view", num, "-R", repo, "--json", "state,mergedAt")
        d = json.loads(out)
        return "merged" if d.get("mergedAt") else d["state"].lower()

    def merge_pr(self, pr_url) -> None:
        repo, num = self._split(pr_url)
        self._gh("pr", "merge", num, "-R", repo, "--squash", "--delete-branch")

    def dispatch(self, repo, workflow) -> None:
        self._gh("workflow", "run", workflow, "-R", repo)

    def last_run(self, repo, workflow) -> tuple[str, str]:
        out = self._gh("run", "list", "-R", repo, "--workflow", workflow, "--limit", "1",
                       "--json", "status,conclusion")
        rows = json.loads(out)
        if not rows:
            return "none", ""
        return rows[0]["status"], rows[0].get("conclusion") or ""


# --- crane (registry-to-registry, no daemon) ---------------------------------


class CraneClient(Protocol):
    def digest(self, ref: str) -> str: ...
    def copy(self, src: str, dst: str) -> None: ...


class CraneCLI:
    """crane resolves digests and copies images between registries without a
    docker daemon; its auth comes from the operator's own docker credential
    helpers (`gcloud auth configure-docker <host>` for Artifact Registry)."""

    def __init__(self, env: dict[str, str] | None = None):
        self._env = tool_env(env)
        if not shutil.which("crane"):
            raise ToolError("crane is not on PATH (brew install crane); `stack upgrade` copies images with it")

    def digest(self, ref) -> str:
        return _run(["crane", "digest", ref], env=self._env).strip()

    def copy(self, src, dst) -> None:
        _run(["crane", "cp", src, dst], timeout=600, env=self._env)


# --- cabal (through its CLI, against a port-forward) ------------------------


class CabalClient(Protocol):
    def login(self, code: str) -> None: ...
    def mint(self, sub: str, scope: str, ttl_days: int) -> str: ...


class CblCLI:
    """`cbl` from a venv, pointed at a port-forward of the cabal Service.

    CABAL_HOME is a directory the installer owns (the login token lands
    there); the operator's own ~/.cabal is never touched.
    """

    def __init__(self, cbl: str, url: str, home: str, forward: Callable[[], subprocess.Popen] | None = None,
                 env: dict[str, str] | None = None):
        # The port-forward is opened on demand, right before each call, and
        # reopened if it is gone: it cannot be opened at startup (the Service
        # does not exist yet on a fresh install) and it dies when the pod it
        # reached restarts (which the fresh-code step does on purpose).
        self._cbl, self._url, self._home, self._forward = cbl, url, home, forward
        self._extra = dict(env or {})
        self._pf: subprocess.Popen | None = None
        os.makedirs(home, mode=0o700, exist_ok=True)

    def _env(self) -> dict[str, str]:
        return tool_env({**self._extra, "CABAL_HOME": self._home, "CABAL_URL": self._url})

    def _port_open(self) -> bool:
        u = urlparse(self._url)
        try:
            with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=1):
                return True
        except OSError:
            return False

    def ensure_tunnel(self, timeout: float = 60.0) -> None:
        """A port-forward that answers, opened or reopened as needed."""
        if self._port_open():
            return
        if self._forward is None:
            raise ToolError(f"nothing answers at {self._url} and no port-forward is configured")
        if self._pf is not None:
            self._pf.terminate()
        self._pf = self._forward()
        deadline = time.monotonic() + timeout
        while not self._port_open():
            if self._pf.poll() is not None or time.monotonic() > deadline:
                raise ToolError(f"the port-forward to cabal at {self._url} did not come up")
            time.sleep(1)

    def close(self) -> None:
        if self._pf is not None:
            self._pf.terminate()

    def login(self, code) -> None:
        self.ensure_tunnel()
        proc = subprocess.run([self._cbl, "login"], input=code + "\n", capture_output=True, text=True,
                              env=self._env())
        if proc.returncode != 0 or "Logged in" not in proc.stdout + proc.stderr:
            raise ToolError(f"cbl login failed: {(proc.stdout + proc.stderr).strip()[-200:]}")

    def mint(self, sub, scope, ttl_days) -> str:
        self.ensure_tunnel()
        proc = subprocess.run([self._cbl, "token", "mint-service", "--sub", sub, "--scope", scope,
                               "--aud", "cabal-core", "--ttl-days", str(ttl_days)],
                              capture_output=True, text=True, env=self._env())
        if proc.returncode != 0:
            raise ToolError(f"cbl token mint-service failed: {proc.stderr.strip()[-200:]}")
        token = proc.stdout.strip().splitlines()[-1].strip()
        if token.count(".") != 2:
            raise ToolError("cbl token mint-service did not print a JWT")
        return token
