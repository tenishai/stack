"""`stack ui`: the same files, behind buttons (stack#12).

A local page over the profiles file. A profile's page shows what its stack
file says, a Check button (preflight), and Install / Upgrade buttons that
run the same engine the command line runs, their lines appearing as they
happen. The page never holds a secret: profiles are paths, Secrets go from
the operator's files straight to the cluster as they do on the command line.
Served on localhost only: one operator, their machine, their credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from stack import envfile as _envfile
from stack import preflight as _preflight
from stack import runs as _runs
from stack.clients import CraneCLI, GcloudCLI, GhCLI, KubectlCLI, ToolError, tool_env
from stack.model import Stack
from stack.profiles import Profile, Profiles
from stack.steps import ref_parts

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def short_image(image: str) -> str:
    """`…/agent-images/kobo:v1@sha256:<64>` as `kobo:v1@sha256:<12>…`: the path is
    the install's own registry and the digest a receipt, not reading matter."""
    head, _, digest = image.partition("@")
    return head.split("/")[-1] + (f"@{digest[:19]}…" if digest else "")


def image_parts(image: str) -> tuple[str, str]:
    """(tag, digest) of an image ref; either may be empty."""
    _, tag, digest = ref_parts(image)
    return tag, digest


TEMPLATES.env.filters["short_image"] = short_image
TEMPLATES.env.filters["image_parts"] = image_parts


def _example_path(profile) -> Path | None:
    """The gojira.env.example the profile's Gojira checkout ships, when one is
    under its repos dir: the gojira repo the stack references (else the first
    clone under the repos dir). The repos dir is the source; a checkout the
    operator has elsewhere is not guessed at."""
    base = Path(profile.repos).expanduser() if profile.repos else None
    guesses = []
    if base is not None:
        stack = None
        try:
            stack = Stack.load(Path(profile.stack_file).expanduser())
        except Exception:
            stack = None
        if stack is not None:
            for app in stack.apps:
                if "gojira" in app.repo.split("/")[-1]:
                    guesses.append(base / app.repo.split("/")[-1] / "gojira.env.example")
                    break
        guesses.append(base / "gojira" / "gojira.env.example")
    for g in guesses:
        if g.exists():
            return g
    return None


def environment(stack: Stack | None) -> str:
    """The environment the stack's apps deploy to, when they agree on one;
    the page wears it (prod in rose) so a press is never made on the wrong one."""
    if stack is None:
        return ""
    envs = {a.environment for a in stack.apps}
    return envs.pop() if len(envs) == 1 else ""


def level_of(run) -> str:
    """A check run's rail: idle until it has run, then its summary line's level."""
    if run is None or run.finished is None or not run.lines:
        return "idle"
    return {"ok": "ok", "warn": "warn", "fail": "fail"}.get(run.lines[-1].level, "idle")


def _app_identity_of(profile: Profile) -> tuple[str, str]:
    """The GitHub App id + pem path the install's env file carries, or ("", "")
    when the file is absent, has neither, or is unreadable. Reading the env file
    here is the same read the install and the env form do; the card only needs
    whether both are present, never their values (the pem path is shown, the
    key is not read or echoed)."""
    if not profile.env_file:
        return ("", "")
    p = Path(profile.env_file).expanduser()
    if not p.exists():
        return ("", "")
    try:
        vals = _envfile.get_all(p.read_text())
    except Exception:
        return ("", "")
    return (vals.get("GOJIRA_GITHUB_APP_ID", ""), vals.get("GOJIRA_GITHUB_APP_PEM", ""))


def _app_usable(app_id: str, app_pem: str) -> bool:
    """Whether the env file's App id + pem is usable enough to gate the install
    off (criterion 3, "when it shows"). All four are local checks — no network:
    both present, the pem path exists, and the pem parses as an RSA private key.
    The key is read only to test it; it is never echoed, logged, or stored
    (only the path is stored). The network checks — a JWT answering GET /app and
    the installation's coverage — are Done's job, not the card's: the card stands
    in for the install until the operator presses Done, and Done runs them."""
    if not app_id or not app_pem:
        return False
    pem = Path(app_pem).expanduser()
    if not pem.exists():
        return False
    try:
        import jwt

        jwt.encode({"iat": 0, "exp": 1, "iss": app_id}, pem.read_text(), algorithm="RS256")
    except Exception:
        return False
    return True


def _app_setup_doc(profile: Profile) -> dict:
    """The GitHub App settings, read from the checkout's docs/github-app-setup.md
    at render time (criterion 3: the table has changed twice this month, so the
    card renders the doc's current table, never a copy in our tree). Returns the
    permission rows, the install steps, and whether the doc was found; a missing
    doc or checkout gets one sentence naming the profile field (the refusals' way).
    The checkout is profile.gojira_repo, else the gojira clone under the repos dir."""
    import re
    candidates = []
    if profile.gojira_repo:
        candidates.append(Path(profile.gojira_repo).expanduser() / "docs" / "github-app-setup.md")
    if profile.repos:
        candidates.append(Path(profile.repos).expanduser() / "gojira" / "docs" / "github-app-setup.md")
    found = next((c for c in candidates if c.exists()), None)
    if found is None:
        return {"found": False, "perms": [], "steps": [], "doc": ""}
    text = found.read_text()
    # the permissions table: | Field | Level | ... rows with a permission name + a level
    perms: list[tuple[str, str]] = []
    for m in re.finditer(r"^\|\s*([A-Za-z][A-Za-z &/]+?)\s*\|\s*(Read[^|]*|Read & write|Write[^|]*)\s*\|", text, re.M):
        name = m.group(1).strip()
        if name in ("Field", "Permission"):
            continue
        perms.append((name, m.group(2).strip()))
    # the numbered install steps (section 3: Install the App)
    steps = [s.strip() for s in re.findall(r"^\s*\d+\.\s+(.+)$", text, re.M)][:6]
    return {"found": True, "perms": perms, "steps": steps, "doc": str(found)}


def make_app(profiles_path: Path, runner: _runs.Runner | None = None,
             perform: Callable = _runs.perform, preflight: Callable = _preflight.preflight,
             crane: Callable = CraneCLI, github: Callable = GhCLI,
             kubectl: Callable = KubectlCLI, gcloud: Callable = GcloudCLI) -> FastAPI:
    app = FastAPI(title="stack")
    runner = runner or _runs.Runner()

    def page(name: str, request: Request, status_code: int = 200, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"runner": runner, **ctx}, status_code=status_code)

    def load_profile(name: str) -> Profile | None:
        return Profiles.load(profiles_path).get(name)

    def notice(request: Request, status_code: int, text: str, *, title: str = "Not done",
               level: str = "fail", link: str = "", link_text: str = "") -> HTMLResponse:
        """A refusal as a page of its own, not bare text: the words a test
        checks are the words the operator reads."""
        return page("notice.html", request, status_code=status_code, title=title, text=text, level=level,
                    link=link, link_text=link_text)

    def stack_or_error(profile: Profile) -> tuple[Stack | None, str]:
        try:
            return Stack.load(profile.target().stack_file), ""
        except Exception as exc:  # yaml / pydantic / missing file: one sentence
            return None, f"{profile.stack_file}: {str(exc).splitlines()[0]}"

    def index_rows():
        rows = []
        for p in Profiles.load(profiles_path).profiles:
            stack, error = stack_or_error(p)
            last = next((r for r in reversed(list(runner.runs.values())) if r.profile == p.name), None)
            rows.append({"profile": p, "stack": stack, "error": error, "env": environment(stack), "last": last})
        return rows

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page("index.html", request, rows=index_rows(), error="")

    @app.post("/profiles")
    def save_profile(request: Request, name: str = Form(), stack_file: str = Form(),
                     kubeconfig: str = Form(""), cloud_account: str = Form(""), repos: str = Form(""),
                     cbl: str = Form(""), env_file: str = Form(""), gojira_repo: str = Form("")):
        try:
            profile = Profile(name=name.strip(), stack_file=stack_file.strip(), kubeconfig=kubeconfig.strip(),
                              cloud_account=cloud_account.strip(), repos=repos.strip(), cbl=cbl.strip(),
                              env_file=env_file.strip(), gojira_repo=gojira_repo.strip())
        except ValueError as exc:
            return page("index.html", request, rows=index_rows(), error=str(exc).splitlines()[0], status_code=422)
        book = Profiles.load(profiles_path)
        book.put(profile)
        book.save(profiles_path)
        return RedirectResponse(f"/p/{profile.name}", status_code=303)

    @app.post("/profiles/{name}/delete")
    def delete_profile(name: str):
        book = Profiles.load(profiles_path)
        book.remove(name)
        book.save(profiles_path)
        return RedirectResponse("/", status_code=303)

    @app.get("/p/{name}", response_class=HTMLResponse)
    def profile_page(name: str, request: Request):
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        stack, error = stack_or_error(profile)
        runs = [r for r in runner.runs.values() if r.profile == name]
        last_check = next((r for r in reversed(runs) if r.kind == "check"), None)
        # Slice 3 (Ember 09): three Gojira states, not two —
        #   installed:    namespace + pods ready + a version  -> version + re-run button
        #   namespace-only: namespace exists but nothing answers -> say so + button
        #   empty:        no namespace                       -> "no Gojira yet" + button
        #   unknown:      kubectl itself is unreadable       -> note only, no button
        # A dead kubectl must never render the empty-state nor an enabled button
        # (the page would be offering to install into a cluster it could not read).
        gojira_state = "unknown"   # installed | namespace-only | empty | unknown
        gojira_version = ""
        gojira_note = ""
        ns = stack.gojira.namespace if stack is not None else "gojira"
        if stack is not None:
            try:
                kc = kubectl(env=profile.target().env)
                if kc.namespace_exists(ns):
                    ready, total = kc.pods_ready(ns)
                    if total and ready == total:
                        gojira_state = "installed"
                        gojira_version = _gojira_version(kc, ns)
                    else:
                        gojira_state = "namespace-only"
                        # a half-failed install, or a rollout where one pod still answers:
                        # "pods not all ready" is true in both; "nothing answers" is not the latter
                        gojira_note = (f"namespace {ns} exists, pods not all ready"
                                       + (f" ({ready}/{total})" if total else " (no pods)"))
                else:
                    gojira_state = "empty"
            except ToolError:
                gojira_state = "unknown"
                gojira_note = f"could not read the cluster in {profile.kubeconfig or 'the current context'}"
        # Slice 4 (criterion 3): the GitHub App is a *pause card*. It stands in for
        # the Gojira row + the Install Gojira button whenever Gojira is not yet on
        # the cluster and the env file has no usable App id + pem. Once installed
        # the card is gone (the gate is passed); an unreadable cluster (unknown)
        # offers nothing, card or button.
        app_id, app_pem = _app_identity_of(profile)
        # The gate passes only once the App is verified (id + pem present, the
        # path exists, the key parses) AND Gojira is installed. An installed
        # cluster shows no card (the gate is passed; see note 13: qa renders the
        # installed state, no card). A cluster Gojira is not on yet shows the card
        # whenever the App is not usable; an unreadable cluster (unknown) offers
        # nothing, card or button.
        show_card = gojira_state != "installed" and gojira_state != "unknown" and not _app_usable(app_id, app_pem)
        # the org the card links to: the org every app's repo lives in (the part
        # before the first /), when they agree; else the first one.
        orgs = {a.repo.split("/")[0] for a in stack.apps} if stack is not None else set()
        stack_org = next(iter(orgs)) if len(orgs) == 1 else (next(iter(orgs)) if orgs else "")
        app_setup = _app_setup_doc(profile)
        return page("profile.html", request, profile=profile, stack=stack, error=error, env=environment(stack),
                    runs=list(reversed(runs)), last_check=last_check, check_level=level_of(last_check),
                    busy=runner.active(), gojira_state=gojira_state, gojira_version=gojira_version,
                    gojira_note=gojira_note, show_card=show_card, app_id=app_id, app_pem=app_pem,
                    stack_org=stack_org, app_setup=app_setup)

    @app.post("/p/{name}/check")
    def check(name: str, request: Request):
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        stack, error = stack_or_error(profile)
        if stack is None:
            return notice(request, 422, error, link=f"/p/{name}", link_text=f"Back to {name}")

        def work(run: _runs.Run):
            section = None
            checks = preflight(stack, profile.target())
            for c in checks:
                head = c.what.split(":")[0] if ":" in c.what else "tools"
                if head != section:
                    run.add("head", head)
                    section = head
                run.add(c.status, f"{c.what}" + (f" — {c.detail}" if c.detail else ""))
            fails = sum(1 for c in checks if c.status == "fail")
            warns = sum(1 for c in checks if c.status == "warn")
            run.add("fail" if fails else "warn" if warns else "ok",
                    f"{fails} missing, {warns} to look at" if fails or warns else "nothing missing")

        return _started(request, runner.start(name, "check", "preflight", work))

    @app.post("/p/{name}/install")
    def start_install(name: str, request: Request, apps: list[str] = Form([])):
        """Install the ticked apps, or every app. Upgrades go through the
        table's release picker only: one surface, one decision per app."""
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        target = profile.target()

        def work(run: _runs.Run):
            # The press is the review: PRs this run opens on the install's own repos are merged by it.
            report = perform(target, "install", apps, merge_setup_prs=True, merge_pins=True, say=run.say)
            run.add("ok", f"done: {len(report.done)} steps")

        return _started(request, runner.start(name, "install", ", ".join(apps) or "every app", work))

    @app.get("/p/{name}/env", response_class=HTMLResponse)
    def env_page(name: str, request: Request):
        """The install's gojira.env, as a form. Values come from the file, docs
        from the profile's gojira.env.example, and the picks (PROJECT, CLUSTER)
        list what this machine can see — gcloud's projects and the kubeconfig's
        contexts — so the operator can tell a name from the project it holds."""
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        env_path = Path(profile.env_file).expanduser() if profile.env_file else None
        env_error = ""
        raw = ""
        if env_path is None:
            env_error = "this profile has no env file yet — set one in Profile settings (Gojira env file)"
        elif not env_path.exists():
            env_error = f"no such file: {env_path}"
        else:
            raw = env_path.read_text()

        # docs: the example's one-liner where it has one, else the page's own
        example = _example_path(profile)
        example_docs = _envfile.example_docs(example.read_text()) if example and example.exists() else {}
        docs = {k: (example_docs.get(k) or fallback) for k, fallback in _envfile.KNOWN_KNOBS}
        values = _envfile.get_all(raw) if raw else {k: "" for k, _ in _envfile.KNOWN_KNOBS}

        contexts: list[str] = []
        ctx_note = ""
        if not profile.kubeconfig:
            ctx_note = "no kubeconfig pinned on the profile"
        else:
            try:
                contexts = kubectl(env=profile.target().env).list_contexts()
            except ToolError:
                ctx_note = f"could not read the contexts in {profile.kubeconfig}"
        projects: list[dict] = []
        account = ""
        try:
            gc = gcloud(env=profile.target().env)
            projects = gc.projects()
            account = gc.account()
        except ToolError:  # a dead gcloud is a note, not a page error
            projects, account = [], ""
        return page("env.html", request, profile=profile, env_path=env_path, env_error=env_error,
                    values=values, docs=docs, contexts=contexts, ctx_note=ctx_note, projects=projects,
                    account=account)

    @app.post("/p/{name}/env")
    async def env_save(name: str, request: Request):
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        if not profile.env_file:
            return notice(request, 422, "this profile has no env file to save to; set one in Profile settings first",
                          level="warn", link=f"/p/{name}", link_text=f"Back to {name}")
        form = await request.form()
        # every knob the form shows is passed through, empty included: a key the
        # file carries and the operator cleared becomes `KEY=` (env.sh reads it as
        # unset); a key not in the file and empty is simply not appended.
        values = {k: str(form.get(k) or "").strip() for k in _envfile.KNOWN_KEYS}
        try:
            _envfile.save_env_file(Path(profile.env_file).expanduser(), values)
        except ToolError as exc:
            return notice(request, 422, str(exc), level="warn", link=f"/p/{name}/env", link_text="Back to the form")
        return RedirectResponse(f"/p/{name}/env", status_code=303)

    @app.get("/p/{name}/tags/{app_name}", response_class=HTMLResponse)
    def tags(name: str, app_name: str, request: Request):
        """The releases at the app's source_image, as a pick-one form; loaded
        into the app table after the page, since crane takes a moment."""
        profile = load_profile(name)
        stack, error = stack_or_error(profile) if profile else (None, "no such profile")
        app_ = next((a for a in stack.apps if a.name == app_name), None) if stack else None
        if app_ is None:
            return HTMLResponse(error or f"no app {app_name!r}", status_code=404)
        if profile is None:
            return HTMLResponse("no such profile", status_code=404)
        env = profile.target().env
        try:
            tags = _runs.available_tags(app_, crane(env))
        except ToolError as exc:
            return page("_tags.html", request, profile=profile, app=app_, tags=[], current="", pvc="", error=str(exc))
        _, current, _ = ref_parts(app_.image)
        return page("_tags.html", request, profile=profile, app=app_, tags=tags, current=current,
                    pvc=_runs.data_pvc(app_, github(env)), busy=runner.active(), error="")

    @app.post("/p/{name}/upgrade-to")
    def upgrade_to(name: str, request: Request, app_name: str = Form(alias="app"), tag: str = Form(""),
                   yes: bool = Form(False)):
        """Pin the release in the stack file, then run the upgrade for that app."""
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        if runner.active():
            return _started(request, None)
        target = profile.target()
        stack, error = stack_or_error(profile)
        app_ = next((a for a in stack.apps if a.name == app_name), None) if stack else None
        back = dict(link=f"/p/{name}", link_text=f"Back to {name}")
        if app_ is None:
            return notice(request, 422, error or f"no app {app_name!r}", **back)
        # Every refusal comes before anything is written: a stack file that
        # pins what the cluster doesn't run is drift nobody decided on.
        if not tag:
            return notice(request, 422, f"{app_name}: pick a release first", level="warn", **back)
        if tag == ref_parts(app_.image)[1]:
            return notice(request, 422, f"{app_name}: already pinned to {tag}", level="warn", **back)
        pvc = _runs.data_pvc(app_, github(target.env))
        if pvc and not yes:
            return notice(request, 422, f"{app_name}: {pvc} holds its data; tick the backup box first",
                          level="warn", **back)

        def work(run: _runs.Run):
            old, new = _runs.set_image(target, app_name, tag, crane(target.env))   # refuses before writing
            run.add("info", f"{app_name}: stack file now pins {new} (was {old})")
            report = perform(target, "upgrade", [app_name], merge_setup_prs=True, merge_pins=True, yes=yes,
                             say=run.say)
            run.add("ok", f"done: {len(report.done)} steps")

        return _started(request, runner.start(name, "upgrade", f"{app_name} to {tag}", work))

    def _started(request: Request, run: _runs.Run | None):
        if run is None:
            # a run can finish in the gap between start() returning None and active();
            # then there is no active run and nothing to point at
            active = runner.active()
            if active is None:
                return notice(request, 409, "a run just finished; press again", level="info", link="/", link_text="Profiles")
            return notice(request, 409, f"a run is already going: #{active.id} {active.kind} on {active.profile}",
                          title="One run at a time", level="warn", link=f"/runs/{active.id}", link_text="Watch it")
        return RedirectResponse(f"/runs/{run.id}", status_code=303)

    @app.post("/p/{name}/install-gojira")
    def install_gojira(name: str, request: Request):
        """Slice 3 (criteria 2, 4): the Gojira underneath the stack. One press runs
        the profile's Gojira checkout's `scripts/install.sh` as a subprocess under
        the pinned env — `GOJIRA_ENV` points at the profile's env file, so install.sh
        reads the values it sourced there; nothing is sourced into this process and
        no env value ever appears on a run line. Done means the Gojira pod is ready
        and `/healthz` answers with a version. Refused, with a sentence naming which,
        when the profile has no env file or no Gojira checkout."""
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        env_path = Path(profile.env_file).expanduser() if profile.env_file else None
        repo = Path(profile.gojira_repo).expanduser() if profile.gojira_repo else None
        # Refused before anything runs: name the thing that is missing, and only it.
        if env_path is None:
            return notice(request, 422, "Install Gojira needs the profile's env file (Gojira env file) set first",
                          level="warn", link=f"/p/{name}/env", link_text="Go to the env form")
        if not env_path.exists():
            return notice(request, 422, f"no such env file: {env_path}", level="warn",
                          link=f"/p/{name}/env", link_text="Go to the env form")
        # the checkout: gojira_repo if set, else the repos dir's gojira clone
        if repo is None and profile.repos:
            repo = Path(profile.repos).expanduser() / "gojira"
        if repo is None or not (repo / "scripts" / "install.sh").exists():
            return notice(request, 422, "Install Gojira needs the profile's Gojira checkout (gojira_repo) with a "
                                        "scripts/install.sh; set it in Profile settings",
                          level="warn", link=f"/p/{name}", link_text=f"Back to {name}")
        stack, _ = stack_or_error(profile)
        target = profile.target()
        ns = stack.gojira.namespace if stack else "gojira"
        target_env = target.env
        target_env["GOJIRA_ENV"] = str(env_path)

        def work(run: _runs.Run):
            run.add("head", "install.gojira")
            run.add("info", f"running {repo / 'scripts' / 'install.sh'} under the env file {env_path}")
            rc = _run_install(repo, target_env, run)
            if rc != 0:
                # a non-zero exit is a stop: raise so the run is marked failed the way
                # every other run is (the script's own last line is already a red stop)
                raise ToolError(f"scripts/install.sh exited {rc}")
            # done means the Gojira pod is ready and /healthz answers with a version
            kc = kubectl(env=target_env)
            try:
                ready, total = kc.pods_ready(ns)
            except ToolError:
                ready, total = 0, 0
            if not total or ready != total:
                run.add("fail", f"stopped: {ready}/{total} Gojira pods ready")
                run.ok = False
                return
            run.add("ok", f"{ready}/{total} Gojira pods ready")
            # the version: /healthz (the port-forwarded Gojira) primarily, the
            # gojira-install ConfigMap as fallback. open_gojira gives the client;
            # a ToolError there (namespace gone between the check and now) is not
            # a failed install — the pods are ready; fall back to the ConfigMap.
            gojira = None
            if stack is not None:
                try:
                    _, gojira = _runs.open_gojira(stack, target, say=run.say)
                except ToolError:
                    gojira = None
            try:
                version = _gojira_version(kc, ns, gojira)
            finally:
                if gojira is not None:
                    gojira.close()
            if version and version != "running, version unknown":
                run.add("ok", f"Gojira {version}")
            else:
                run.add("warn", "Gojira is up; could not read its version")

        return _started(request, runner.start(name, "install.gojira", "Gojira (scripts/install.sh)", work))

    @app.post("/p/{name}/app-card")
    async def app_card_done(name: str, request: Request):
        """Slice 4 (criterion 3): the pause card's Done button. The operator has
        typed the App id and the pem path; Done runs the three checks and says
        which failed: (a)+(b) the pem parses and a JWT signed with it answers GET
        /app for that id (one call, app_identity), and (c) the App's installation
        reaches every repo the stack file names (or is installed on all).
        Nothing is stored but the id and the path, in the install's env file
        (save_env_file, byte-for-byte lossless for the rest). A failure names the
        check and stays on the card; a pass writes the two keys and redirects
        back to the profile, where the card is now gone."""
        profile = load_profile(name)
        if profile is None:
            return notice(request, 404, f"no profile {name!r}", title="No such profile", link="/", link_text="Profiles")
        if not profile.env_file:
            return notice(request, 422, "the card stores the id and pem path in the install's env file; "
                                        "this profile has no env file — set one in Profile settings first",
                          level="warn", link=f"/p/{name}", link_text=f"Back to {name}")
        form = await request.form()
        app_id = str(form.get("app_id") or "").strip()
        pem = str(form.get("app_pem") or "").strip()
        if not app_id or not pem:
            return notice(request, 422, "both the App id and the pem path are needed before Done can check",
                          level="warn", link=f"/p/{name}", link_text=f"Back to {name}")
        gh = github(env=profile.target().env)
        # (a)+(b) then (c), in order: the first failure names itself and stays on
        # the card. Both methods get the id and the pem's PATH and read the key
        # themselves, used only to sign; its bytes never appear here or on a page.
        try:
            gh.app_identity(app_id, pem)
        except ToolError as exc:
            return notice(request, 422, f"the App check failed: {exc}", level="warn",
                          link=f"/p/{name}", link_text=f"Back to {name}")
        # (c) the installation covers the repos the stack file names
        stack, error = stack_or_error(profile)
        repos: list[str] = []
        if stack is not None:
            repos = sorted({a.repo for a in stack.apps} | {a.deploy_repo for a in stack.apps if a.deploy_repo})
        try:
            gh.installation_covers(app_id, pem, repos)
        except ToolError as exc:
            return notice(request, 422, f"the App check failed: {exc}", level="warn",
                          link=f"/p/{name}", link_text=f"Back to {name}")
        # pass: store only the id and the path, lossless for the rest of the file
        try:
            _envfile.save_env_file(Path(profile.env_file).expanduser(),
                                   {"GOJIRA_GITHUB_APP_ID": app_id, "GOJIRA_GITHUB_APP_PEM": pem})
        except ToolError as exc:
            return notice(request, 422, f"checked, but could not write the env file: {exc}", level="warn",
                          link=f"/p/{name}", link_text=f"Back to {name}")
        return RedirectResponse(f"/p/{name}", status_code=303)

    def _gojira_version(kubectl, ns: str, gojira=None) -> str:
        """The running version, read where Gojira actually puts it. `GET /healthz`
        answers {"ok": true, "version": ...} (gojira web/__init__.py); the ConfigMap
        Gojira writes is `gojira-install`/`GOJIRA_VERSION` (bootstrap.sh), not
        `gojira-version`/`version` — that one is never written, so it was the wrong
        source. Installs older than v1.0.0 have neither: "running, version unknown",
        never a blank."""
        try:
            if gojira is not None:
                v = (gojira.health() or {}).get("version")
                if v:
                    return v
        except ToolError:
            pass
        try:
            v = kubectl.configmap_value(ns, "gojira-install", "GOJIRA_VERSION")
            if v:
                return v
        except ToolError:
            pass
        return "running, version unknown"

    def _run_install(repo: Path, env: dict[str, str], run: _runs.Run) -> int:
        """Run the checkout's scripts/install.sh as a subprocess, streaming its
        stdout line by line onto the run. The values travel in the environment
        (install.sh sources gojira.env from GOJIRA_ENV), never on the command
        line, so no value appears on a run line. Returns the exit code; a non-zero
        exit is a stop the script's own last line carries."""
        import subprocess
        script = str(repo / "scripts" / "install.sh")
        proc = subprocess.Popen(["bash", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, env=tool_env(env))
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            run.add("info", line)
        return proc.wait()

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(run_id: int, request: Request):
        run = runner.runs.get(run_id)
        if run is None:
            return notice(request, 404, f"no run #{run_id} (runs live only while `stack ui` does)",
                          title="No such run", link="/", link_text="Profiles")
        profile = load_profile(run.profile)
        stack = stack_or_error(profile)[0] if profile else None
        return page("run.html", request, run=run, env=environment(stack))

    @app.get("/runs/{run_id}/lines", response_class=HTMLResponse)
    def run_lines(run_id: int, request: Request):
        run = runner.runs.get(run_id)
        if run is None:
            return HTMLResponse("", status_code=404)
        return page("_lines.html", request, run=run, oob=True)   # the polled response also refreshes the header badge

    return app


def serve(profiles_path: Path, port: int) -> None:
    import uvicorn

    print(f"stack ui on http://127.0.0.1:{port}  (profiles: {profiles_path})")
    uvicorn.run(make_app(profiles_path), host="127.0.0.1", port=port, log_level="warning")


__all__ = ["make_app", "serve", "ToolError"]
