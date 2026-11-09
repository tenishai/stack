"""Fakes for the four hands, shaped like what the dogfood answered on 2026-10-08."""

from __future__ import annotations

from stack.clients import AppRow, ToolError


class FakeGojira:
    def __init__(self, apps: list[AppRow] | None = None, clusters: list[dict] | None = None):
        self.connected: dict[str, str] = {}   # app name -> repo it was connected with
        self._apps = {a.id: a for a in (apps or [])}
        self._clusters = clusters or []
        self.calls: list[str] = []
        # what a fresh connect comes back as, by app name (default awaiting_pr with a PR)
        self.connect_outcome: dict[str, tuple[str, str]] = {}
        self.sync_outcome: dict[int, str] = {}
        self.provision_outcomes: dict[int, list[str]] = {}

    def api_token(self) -> str:
        return "tok-123"

    def clusters(self):
        return list(self._clusters)

    def register_cluster(self, name, gcp_project, location, gke_cluster, ar_repo):
        self.calls.append(f"register:{name}")
        self._clusters.append({"name": name, "gcp_project": gcp_project, "location": location,
                               "gke_cluster": gke_cluster, "ar_repo": ar_repo})

    def apps(self):
        return list(self._apps.values())

    def app(self, app_id):
        return self._apps[app_id]

    def connect(self, name, repo, workload_class, environment, namespace, auto_merge):
        self.connected[name] = repo
        self.calls.append(f"connect:{name}:auto={auto_merge}")
        status, url = self.connect_outcome.get(name, ("awaiting_pr", f"https://github.com/{repo}/pull/1"))
        row = AppRow(id=len(self._apps) + 1, name=name, status=status, setup_pr_url=url)
        self._apps[row.id] = row
        return row

    def provision(self, app_id):
        self.calls.append(f"provision:{app_id}")
        outcomes = self.provision_outcomes.get(app_id, [])
        self._apps[app_id].status = outcomes.pop(0) if outcomes else "active"

    def sync(self, app_id):
        self.calls.append(f"sync:{app_id}")
        self._apps[app_id].status = self.sync_outcome.get(app_id, "active")
        return self._apps[app_id]


class FakeKubectl:
    def __init__(self, ready: dict[str, tuple[int, int]] | None = None, log_code: str = "ABC123",
                 images: dict[str, str] | None = None):
        self.namespaces: set[str] = set()
        self.secrets: dict[tuple[str, str], dict[str, str]] = {}
        self.configmaps: dict[tuple[str, str], dict[str, str]] = {}
        self.applied: list[str] = []
        self.ready = ready or {}
        self.images = images or {}   # ns -> what the deployment runs
        self.log_code = log_code
        self.code_after_restart = "FRESH1"
        self.calls: list[str] = []

    def current_context(self):
        return "fake-context"

    def namespace_exists(self, ns):
        return ns in self.namespaces

    def secret_exists(self, ns, name):
        return (ns, name) in self.secrets

    def secret_value(self, ns, name, key):
        return self.secrets[(ns, name)][key]

    def apply_secret(self, ns, name, data):
        self.calls.append(f"secret:{ns}/{name}")
        self.secrets[(ns, name)] = dict(data)

    def configmap_exists(self, ns, name):
        return (ns, name) in self.configmaps

    def apply_configmap(self, ns, name, data):
        self.calls.append(f"configmap:{ns}/{name}")
        self.configmaps[(ns, name)] = dict(data)

    def apply_file(self, path):
        self.applied.append(path)
        return "clusterrole.rbac.authorization.k8s.io/x created"

    def pods_ready(self, ns):
        return self.ready.get(ns, (1, 1))

    def deployed_image(self, ns, deploy):
        return self.images.get(ns, "")

    def logs_grep(self, ns, deploy, pattern, since="10m"):
        return self.log_code

    def restart(self, ns, deploy):
        self.calls.append(f"restart:{ns}/{deploy}")
        self.log_code = self.code_after_restart

    def port_forward(self, ns, target, local, remote):
        raise AssertionError("fakes never port-forward")


class FakeGitHub:
    def __init__(self, states: dict[str, str] | None = None, runs: dict[str, tuple[str, str]] | None = None,
                 repos: dict[str, dict[str, str]] | None = None):
        self.states = states or {}
        self.runs = runs or {}
        self.outcomes: dict[str, list[str]] = {}
        self.calls: list[str] = []
        self.repos: dict[str, dict[str, str]] = repos if repos is not None else {}   # repo -> path -> content
        # what a freshly opened pin PR's state reads as; "merged" lands its files
        self.open_pr_state = "merged"
        self.pr_files: dict[str, dict[str, str]] = {}
        self.user = "tester"                  # "" = not logged in
        self.org_denied: dict[str, str] = {}  # org -> why gh cannot see it

    def login(self):
        if not self.user:
            raise ToolError("gh is not logged in (gh auth login)")
        return self.user

    def org_access(self, org):
        return self.org_denied.get(org, "")

    def repo_exists(self, repo):
        return repo in self.repos

    def create_repo(self, repo):
        self.calls.append(f"create:{repo}")
        self.repos[repo] = {}

    def push_files(self, repo, files, message):
        current = self.repos[repo]
        changed = sum(1 for k, v in files.items() if current.get(k) != v)
        if changed:
            self.calls.append(f"push:{repo}:{changed}")
            current.update(files)
        return changed

    def read_file(self, repo, path):
        try:
            return self.repos[repo][path]
        except KeyError:
            raise ToolError(f"{repo}: no {path}") from None

    def open_pr(self, repo, files, title, body, branch):
        url = f"https://github.com/{repo}/pull/{100 + len(self.pr_files)}"
        self.calls.append(f"open_pr:{repo}:{branch}")
        self.pr_files[url] = dict(files)
        self.states[url] = self.open_pr_state
        if self.open_pr_state == "merged":
            self.repos[repo].update(files)
        return url

    def pr_state(self, pr_url):
        return self.states.get(pr_url, "open")

    def merge_pr(self, pr_url):
        self.calls.append(f"merge:{pr_url}")
        self.states[pr_url] = "merged"

    def dispatch(self, repo, workflow):
        self.calls.append(f"dispatch:{repo}:{workflow}")
        # the outcome of each dispatch, in order; default success
        outcomes = self.outcomes.get(repo, [])
        self.runs[repo] = ("completed", outcomes.pop(0) if outcomes else "success")

    def last_run(self, repo, workflow):
        return self.runs.get(repo, ("completed", "success"))


class FakeCrane:
    def __init__(self, digests: dict[str, str] | None = None):
        self.digests = digests or {}   # ref -> digest
        self.copied: list[tuple[str, str]] = []

    def digest(self, ref):
        if ref not in self.digests:
            raise ToolError(f"{ref}: manifest unknown")
        return self.digests[ref]

    def copy(self, src, dst):
        if src not in self.digests:
            raise ToolError(f"{src}: manifest unknown")
        self.copied.append((src, dst))
        self.digests[dst] = self.digests[src]


class FakeCabal:
    def __init__(self, expired: set[str] | None = None):
        self.logged_in: str | None = None
        self.minted: list[tuple[str, str, int]] = []
        self.expired = expired or set()

    def login(self, code):
        if len(code) != 6:
            raise ToolError("bad code")
        if code in self.expired:
            raise ToolError("cbl login failed: code expired")
        self.logged_in = code

    def mint(self, sub, scope, ttl_days):
        self.minted.append((sub, scope, ttl_days))
        return f"h.{sub.replace(':', '-')}-{scope}.s"
