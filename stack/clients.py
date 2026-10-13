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


def _run(args: list[str], *, stdin: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(args, input=stdin, capture_output=True, text=True)
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
    def namespace_exists(self, ns: str) -> bool: ...
    def secret_exists(self, ns: str, name: str) -> bool: ...
    def secret_value(self, ns: str, name: str, key: str) -> str: ...
    def apply_secret(self, ns: str, name: str, data: dict[str, str]) -> None: ...
    def configmap_exists(self, ns: str, name: str) -> bool: ...
    def apply_configmap(self, ns: str, name: str, data: dict[str, str]) -> None: ...
    def apply_file(self, path: str) -> str: ...
    def pods_ready(self, ns: str) -> tuple[int, int]: ...
    def logs_grep(self, ns: str, deploy: str, pattern: str) -> str | None: ...
    def restart(self, ns: str, deploy: str) -> None: ...
    def port_forward(self, ns: str, target: str, local: int, remote: int) -> subprocess.Popen: ...


class KubectlCLI:
    def __init__(self, context: str = ""):
        self._ctx = ["--context", context] if context else []
        if not shutil.which("kubectl"):
            raise ToolError("kubectl is not on PATH")

    def _k(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        return _run(["kubectl", *self._ctx, *args], stdin=stdin, check=check)

    def _exists(self, ns: str, kind: str, name: str) -> bool:
        proc = subprocess.run(["kubectl", *self._ctx, "-n", ns, "get", kind, name],
                              capture_output=True, text=True)
        return proc.returncode == 0

    def namespace_exists(self, ns: str) -> bool:
        return subprocess.run(["kubectl", *self._ctx, "get", "ns", ns],
                              capture_output=True).returncode == 0

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

    def restart(self, ns, deploy) -> None:
        self._k("-n", ns, "rollout", "restart", f"deploy/{deploy}")
        self._k("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")

    def port_forward(self, ns, target, local, remote) -> subprocess.Popen:
        return subprocess.Popen(["kubectl", *self._ctx, "-n", ns, "port-forward", target, f"{local}:{remote}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- gh ---------------------------------------------------------------------


class GitHubClient(Protocol):
    def pr_state(self, pr_url: str) -> str: ...
    def merge_pr(self, pr_url: str) -> None: ...
    def dispatch(self, repo: str, workflow: str) -> None: ...
    def last_run(self, repo: str, workflow: str) -> tuple[str, str]: ...


class GhCLI:
    def __init__(self):
        if not shutil.which("gh"):
            raise ToolError("gh is not on PATH")

    @staticmethod
    def _split(pr_url: str) -> tuple[str, str]:
        m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)$", pr_url)
        if not m:
            raise ToolError(f"not a pull request URL: {pr_url}")
        return m.group(1), m.group(2)

    def pr_state(self, pr_url) -> str:
        repo, num = self._split(pr_url)
        out = _run(["gh", "pr", "view", num, "-R", repo, "--json", "state,mergedAt"])
        d = json.loads(out)
        return "merged" if d.get("mergedAt") else d["state"].lower()

    def merge_pr(self, pr_url) -> None:
        repo, num = self._split(pr_url)
        _run(["gh", "pr", "merge", num, "-R", repo, "--squash", "--delete-branch"])

    def dispatch(self, repo, workflow) -> None:
        _run(["gh", "workflow", "run", workflow, "-R", repo])

    def last_run(self, repo, workflow) -> tuple[str, str]:
        out = _run(["gh", "run", "list", "-R", repo, "--workflow", workflow, "--limit", "1",
                    "--json", "status,conclusion"])
        rows = json.loads(out)
        if not rows:
            return "none", ""
        return rows[0]["status"], rows[0].get("conclusion") or ""


# --- cabal (through its CLI, against a port-forward) ------------------------


class CabalClient(Protocol):
    def login(self, code: str) -> None: ...
    def mint(self, sub: str, scope: str, ttl_days: int) -> str: ...


class CblCLI:
    """`cbl` from a venv, pointed at a port-forward of the cabal Service.

    CABAL_HOME is a directory the installer owns (the login token lands
    there); the operator's own ~/.cabal is never touched.
    """

    def __init__(self, cbl: str, url: str, home: str, forward: Callable[[], subprocess.Popen] | None = None):
        # The port-forward is opened on demand, right before each call, and
        # reopened if it is gone: it cannot be opened at startup (the Service
        # does not exist yet on a fresh install) and it dies when the pod it
        # reached restarts (which the fresh-code step does on purpose).
        self._cbl, self._url, self._home, self._forward = cbl, url, home, forward
        self._pf: subprocess.Popen | None = None
        os.makedirs(home, mode=0o700, exist_ok=True)

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update({"CABAL_HOME": self._home, "CABAL_URL": self._url})
        return env

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
