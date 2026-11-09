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

from stack import preflight as _preflight
from stack import runs as _runs
from stack.clients import CraneCLI, ToolError
from stack.model import Stack
from stack.profiles import Profile, Profiles
from stack.steps import ref_parts

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ACTIONS = ("install", "upgrade")


def make_app(profiles_path: Path, runner: _runs.Runner | None = None,
             perform: Callable = _runs.perform, preflight: Callable = _preflight.preflight,
             crane: Callable = CraneCLI) -> FastAPI:
    app = FastAPI(title="stack")
    runner = runner or _runs.Runner()

    def page(name: str, request: Request, status_code: int = 200, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"runner": runner, **ctx}, status_code=status_code)

    def load_profile(name: str) -> Profile | None:
        return Profiles.load(profiles_path).get(name)

    def stack_or_error(profile: Profile) -> tuple[Stack | None, str]:
        try:
            return Stack.load(profile.target().stack_file), ""
        except Exception as exc:  # yaml / pydantic / missing file: one sentence
            return None, f"{profile.stack_file}: {str(exc).splitlines()[0]}"

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page("index.html", request, profiles=Profiles.load(profiles_path).profiles, error="")

    @app.post("/profiles")
    def save_profile(request: Request, name: str = Form(), stack_file: str = Form(),
                     kubeconfig: str = Form(""), cloud_account: str = Form(""), repos: str = Form(""),
                     cbl: str = Form(""), env_file: str = Form("")):
        try:
            profile = Profile(name=name.strip(), stack_file=stack_file.strip(), kubeconfig=kubeconfig.strip(),
                              cloud_account=cloud_account.strip(), repos=repos.strip(), cbl=cbl.strip(),
                              env_file=env_file.strip())
        except ValueError as exc:
            return page("index.html", request, profiles=Profiles.load(profiles_path).profiles,
                        error=str(exc).splitlines()[0], status_code=422)
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
            return HTMLResponse(f"no profile {name!r}", status_code=404)
        stack, error = stack_or_error(profile)
        runs = [r for r in runner.runs.values() if r.profile == name]
        last_check = next((r for r in reversed(runs) if r.kind == "check"), None)
        return page("profile.html", request, profile=profile, stack=stack, error=error,
                    runs=list(reversed(runs)), last_check=last_check, busy=runner.active())

    @app.post("/p/{name}/check")
    def check(name: str):
        profile = load_profile(name)
        if profile is None:
            return HTMLResponse(f"no profile {name!r}", status_code=404)
        stack, error = stack_or_error(profile)
        if stack is None:
            return HTMLResponse(error, status_code=422)

        def work(run: _runs.Run):
            for c in preflight(stack, profile.target()):
                run.add(c.status, f"{c.what}" + (f" — {c.detail}" if c.detail else ""))

        return _started(runner.start(name, "check", "preflight", work))

    @app.post("/p/{name}/run")
    def start_run(name: str, action: str = Form(), apps: list[str] = Form([]), yes: bool = Form(False)):
        profile = load_profile(name)
        if profile is None:
            return HTMLResponse(f"no profile {name!r}", status_code=404)
        if action not in ACTIONS:
            return HTMLResponse(f"no such action {action!r}", status_code=422)
        if action == "upgrade" and not apps:
            return HTMLResponse("tick the apps to upgrade (Install with none ticked is every app; "
                                "Upgrade is not)", status_code=422)
        target = profile.target()

        def work(run: _runs.Run):
            # The press is the review: PRs this run opens on the install's own repos are merged by it.
            report = perform(target, action, apps, merge_setup_prs=True, merge_pins=True, yes=yes, say=run.say)
            run.add("ok", f"done: {len(report.done)} steps"
                    + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))

        return _started(runner.start(name, action, ", ".join(apps) or "every app", work))

    @app.get("/p/{name}/tags/{app_name}", response_class=HTMLResponse)
    def tags(name: str, app_name: str, request: Request):
        """The releases at the app's source_image, as a pick-one form; loaded
        into the app table after the page, since crane takes a moment."""
        profile = load_profile(name)
        stack, error = stack_or_error(profile) if profile else (None, "no such profile")
        app_ = next((a for a in stack.apps if a.name == app_name), None) if stack else None
        if app_ is None:
            return HTMLResponse(error or f"no app {app_name!r}", status_code=404)
        try:
            tags = _runs.available_tags(app_, crane(profile.target().env))
        except ToolError as exc:
            return page("_tags.html", request, profile=profile, app=app_, tags=[], current="", error=str(exc))
        _, current, _ = ref_parts(app_.image)
        return page("_tags.html", request, profile=profile, app=app_, tags=tags, current=current, error="")

    @app.post("/p/{name}/upgrade-to")
    def upgrade_to(name: str, app_name: str = Form(alias="app"), tag: str = Form(), yes: bool = Form(False)):
        """Pin the release in the stack file, then run the upgrade for that app."""
        profile = load_profile(name)
        if profile is None:
            return HTMLResponse(f"no profile {name!r}", status_code=404)
        if runner.active():
            return _started(None)
        target = profile.target()
        try:
            old, new = _runs.set_image(target, app_name, tag, crane(target.env))
        except ToolError as exc:
            return HTMLResponse(f"not pinned: {exc}", status_code=422)

        def work(run: _runs.Run):
            run.add("info", f"{app_name}: stack file now pins {new} (was {old})")
            report = perform(target, "upgrade", [app_name], merge_setup_prs=True, merge_pins=True, yes=yes,
                             say=run.say)
            run.add("ok", f"done: {len(report.done)} steps"
                    + (f", waited on {len(report.waited_on)} merges" if report.waited_on else ""))

        return _started(runner.start(name, "upgrade", f"{app_name} to {tag}", work))

    def _started(run: _runs.Run | None):
        if run is None:
            active = runner.active()
            return HTMLResponse(f"a run is already going: <a href='/runs/{active.id}'>#{active.id} "
                                f"{active.kind} on {active.profile}</a>", status_code=409)
        return RedirectResponse(f"/runs/{run.id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(run_id: int, request: Request):
        run = runner.runs.get(run_id)
        if run is None:
            return HTMLResponse(f"no run #{run_id} (runs live only while `stack ui` does)", status_code=404)
        return page("run.html", request, run=run)

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
