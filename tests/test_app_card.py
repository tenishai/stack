"""Slice 4 (criterion 3): the GitHub App pause card.

The profile page shows a card — instead of Check's Gojira row and the Install
Gojira button — whenever the install's env file has no usable App id + pem (no
id, no pem, a pem path that does not exist, or a key that does not parse as RSA).
Done on the card runs the three checks (a) pem parses, (b) a JWT signed with it
answers GET /app for that id, (c) the installation covers the stack's repos —
naming the first failure — and on success stores only the id and the pem path in
the env file (lossless for the rest) and returns with the card gone. The pem's
bytes never appear in a page or run line.

Every test here fails at feb167f (the card, the Done endpoint, and the client's
two methods did not exist).
"""

from pathlib import Path

from fastapi.testclient import TestClient

from stack.preflight import Check
from stack.runs import Runner
from stack.ui import make_app
from tests.fakes import FakeGitHub, FakeKubectl

SECRET_PEM_BODY = "FAKE-PEM-BODY-DO-NOT-PRINT-7F3A"   # a known body to grep for
APP_ID = "12345"
NS = "gojira"


def _stack_file(tmp_path: Path) -> Path:
    f = tmp_path / "s.yaml"
    f.write_text(
        "cluster: {name: gojira-sandbox, gcp_project: vidios-qa, location: us-central1}\n"
        "apps:\n  - {name: kobo, repo: tenishai/gojira, deploy_repo: tenishai/kobo-deploy,\n"
        "     image: r/agent-images/kobo:1, source_image: ghcr.io/tenishai/kobo}\n")
    return f


def _rsa_pem(tmp_path: Path) -> str:
    """A real generated RSA private key (the note: the RSA check must be real, not
    a fake) written to a pem in the tmp dir; returns its path."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    p = tmp_path / "app.pem"
    p.write_bytes(pem)
    return str(p)


class _NoProj:
    def __init__(self, **kw):
        pass

    def account(self):
        return "me@example.com"

    def projects(self):
        return []


def _k8s(ns_exists: bool = True, ready=(1, 1), *, version: str | None = "v1.3.1"):
    k = FakeKubectl(ready={NS: ready})
    if ns_exists:
        k.ns_set.add(NS)
        if version is not None:
            k.configmaps[(NS, "gojira-install")] = {"GOJIRA_VERSION": version}
    return k


def _setup_doc(tmp_path: Path, present: bool = True) -> Path:
    """A stand-in Gojira checkout carrying docs/github-app-setup.md with the
    permissions table — the card renders this table at render time (the note:
    read the doc, never a copy in our tree)."""
    d = tmp_path / "gojira-checkout"
    (d / "docs").mkdir(parents=True, exist_ok=True)
    if present:
        (d / "docs" / "github-app-setup.md").write_text(
            "# GitHub App setup\n\n"
            "| Field | Level | Why | why |\n"
            "|---|---|---|---|\n"
            "| Contents | Read & write | push | |\n"
            "| Pull requests | Read & write | PRs | |\n"
            "| Workflows | Read & write | dispatch | |\n"
            "| Metadata | Read | always | |\n"
            "| Actions | Read & write | self-hosted | |\n"
            "| Variables | Read & write | secrets | |\n")
    return d


def _site(tmp_path, *, kubectl, github, ns_exists: bool = True, app_lines: bool = True,
          pem: str | None = None, doc: bool = True):
    """A profile whose env file has (or lacks) the two App lines. app_lines=True
    writes a working id + the given pem (a real RSA key by default) so the card
    is absent; app_lines=False strips them so the card shows. `pem` is the path to
    write in the GOJIRA_GITHUB_APP_PEM line; the test chooses it to drive the
    local checks (missing path, non-RSA body, a working key)."""
    profiles_path = tmp_path / "p.yaml"
    stack = _stack_file(tmp_path)
    checkout = _setup_doc(tmp_path, present=doc)
    env = tmp_path / "gojira.env"
    lines = "# install's values\nPROJECT=vidios-qa\nCLUSTER=gojira-sandbox\n"
    if app_lines:
        pem = pem if pem is not None else _rsa_pem(tmp_path)
        lines += f"GOJIRA_GITHUB_APP_ID={APP_ID}\nGOJIRA_GITHUB_APP_PEM={pem}\n"
    env.write_text(lines)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("")
    runner = Runner()

    def check_pf(stackf, target):
        return [Check("tools", "ok", "kubectl on PATH"),
                Check("kobo: Gojira", "ok" if ns_exists else "warn",
                      "active" if ns_exists else "no Gojira on this cluster yet")]

    app = make_app(profiles_path, runner,
                   perform=lambda *a, **k: (_ for _ in ()).throw(AssertionError("not the stack engine")),
                   preflight=check_pf,
                   kubectl=lambda *, env: kubectl, gcloud=lambda *, env: _NoProj(),
                   github=lambda *, env: github)
    client = TestClient(app)
    client.runner, client.env, client.profiles_path, client.github = runner, env, profiles_path, github
    client.post("/profiles", data={"name": "qa", "stack_file": str(stack),
                                   "kubeconfig": str(kubeconfig), "env_file": str(env),
                                   "repos": str(tmp_path), "gojira_repo": str(checkout)})
    return client


# --- the card shows when the env file has no usable App id + pem ---

def test_card_shows_when_env_file_has_no_app_lines(tmp_path):
    """Criterion 3, when it shows: the two App lines absent -> the card renders,
    the Gojira row + Install Gojira button do not."""
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=FakeGitHub(), ns_exists=False, app_lines=False)
    page = site.get("/p/qa").text
    assert "GitHub App" in page                     # the card
    assert "github-app-setup.md" in page            # it reads the checkout's doc
    assert "Contents" in page
    assert "Metadata" in page and "Variables" in page   # the doc's table rows, at render time
    assert 'action="/p/qa/app-card"' in page        # the Done form
    # and it stands IN for the install: the install button's form is hidden while the card shows
    assert 'action="/p/qa/install-gojira"' not in page


def test_card_gone_when_env_file_has_a_working_app(tmp_path):
    """The live-profile case (note 13): a working App (id + a real RSA key) in the
    env file -> no card, the installed state shows."""
    site = _site(tmp_path, kubectl=_k8s(ns_exists=True), github=FakeGitHub(), ns_exists=True, app_lines=True)
    page = site.get("/p/qa").text
    assert "GitHub App" not in page
    assert "is on this cluster" in page and "v1.3.1" in page


def test_card_shows_when_pem_path_missing(tmp_path):
    """When it shows: the id is present but the pem path does not exist -> card."""
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=FakeGitHub(), ns_exists=False,
                 app_lines=True, pem=str(tmp_path / "nope.pem"))
    page = site.get("/p/qa").text
    assert "GitHub App" in page
    assert 'action="/p/qa/install-gojira"' not in page


def test_card_shows_when_pem_is_not_rsa(tmp_path):
    """When it shows: the pem path exists but does not parse as an RSA private key
    (a text file) -> card. This is the local half of (a): the key is read only to
    test it; the card shows it, it does not accept it."""
    bad = tmp_path / "not-a-key.pem"
    bad.write_text("not a key\njust text\n")
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=FakeGitHub(), ns_exists=False,
                 app_lines=True, pem=str(bad))
    page = site.get("/p/qa").text
    assert "GitHub App" in page


# --- Done runs the three checks, naming the first failure ---

def test_done_names_which_check_failed_identity(tmp_path, monkeypatch):
    """(a)+(b) the pem and the JWT: a failure here is named before the coverage
    check runs. The test patches the client (the layer beneath the seam), not the
    handler or app_identity itself — the real handler drives the real method."""
    gh = FakeGitHub()
    gh.identity_fail = "GitHub returned 401: that id is not the App this key signs for"
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=gh, ns_exists=False, app_lines=False)
    r = site.post("/p/qa/app-card", data={"app_id": APP_ID, "app_pem": _rsa_pem(tmp_path)},
                  follow_redirects=False)
    assert r.status_code == 422
    # (a)+(b) named; (c) was never run
    assert "GitHub returned 401" in r.text
    assert gh.calls == [f"app_identity:{APP_ID}"]      # covers: never called


def test_done_names_which_check_failed_coverage(tmp_path):
    """(c) the installation coverage: (a)+(b) pass, (c) fails -> named, and the
    id is NOT written to the env file (it is stored only on a full pass)."""
    gh = FakeGitHub()
    gh.covers_fail = "App is installed on 0 of 2 repos; install it on the stack's repos or on all"
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=gh, ns_exists=False, app_lines=False)
    pem = _rsa_pem(tmp_path)
    r = site.post("/p/qa/app-card", data={"app_id": APP_ID, "app_pem": pem}, follow_redirects=False)
    assert r.status_code == 422
    assert "installed on 0 of 2 repos" in r.text
    # both checks ran, in order, and neither stored anything
    assert gh.calls == [f"app_identity:{APP_ID}", "covers:tenishai/gojira,tenishai/kobo-deploy"]
    vals = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in site.env.read_text().splitlines() if "=" in ln}
    assert "GOJIRA_GITHUB_APP_ID" not in vals          # not written on a failed (c)


# --- the success path stores only the id and the path, lossless ---

def test_done_writes_id_and_path_and_card_goes_away(tmp_path):
    gh = FakeGitHub()   # both checks pass by default
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=gh, ns_exists=False, app_lines=False)
    # a pre-existing line the write must preserve (losslessness for the rest)
    pem = _rsa_pem(tmp_path)
    before = site.env.read_text()
    r = site.post("/p/qa/app-card", data={"app_id": APP_ID, "app_pem": pem}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/qa"
    after = site.env.read_text()
    vals = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in after.splitlines() if "=" in ln}
    assert vals["GOJIRA_GITHUB_APP_ID"] == APP_ID
    assert vals["GOJIRA_GITHUB_APP_PEM"] == pem
    # the pre-existing line is preserved byte-for-byte
    for ln in before.splitlines():
        if ln and not ln.startswith("#") and "=" in ln:
            assert ln in after.splitlines(), f"lost a pre-existing line: {ln!r}"
    # the card is now gone (the gate is passed): re-render the profile
    page = site.get("/p/qa").text
    assert "GitHub App" not in page
    # both checks ran, in order
    assert gh.calls == [f"app_identity:{APP_ID}", "covers:tenishai/gojira,tenishai/kobo-deploy"]


# --- the pem's bytes never appear in a page or run line ---

def test_pem_body_never_on_the_page(tmp_path):
    """The pem's body is never echoed: not on the card (which shows the path, not
    the key), not after a Done that stores it. A grep for a known body finds
    nothing. (The local usable-check reads the key to test it; that read is
    internal and the value is never rendered.)"""
    gh = FakeGitHub()
    pem_path = tmp_path / "app.pem"
    pem_path.write_bytes(b"-----BEGIN PRIVATE KEY-----\n" + SECRET_PEM_BODY.encode() + b"\n-----END PRIVATE KEY-----\n")
    site = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=gh, ns_exists=False, app_lines=False)
    page = site.get("/p/qa").text
    assert SECRET_PEM_BODY not in page
    # and after a Done that would store it (a real key makes the local check pass;
    # the *body* is what must not leak — a fake-body pem does not parse, so use a
    # real key for the store and grep its actual body)
    real = _rsa_pem(tmp_path)
    real_body = Path(real).read_text()
    site2 = _site(tmp_path, kubectl=_k8s(ns_exists=False), github=FakeGitHub(), ns_exists=False, app_lines=False)
    r = site2.post("/p/qa/app-card", data={"app_id": APP_ID, "app_pem": real}, follow_redirects=False)
    assert r.status_code == 303
    page2 = site2.get("/p/qa").text
    assert real_body not in page2 and real_body not in r.text
    # the stored line holds the PATH, never the body
    after = site2.env.read_text()
    assert real_body not in after
    assert real in after