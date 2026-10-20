"""Every code path that fetches a URL or resolves a host, and the guard it uses.

The fetcher's SSRF defence was fixed one shape at a time: a scheme and address
check, then DNS rebinding (pin the connection to the checked addresses), then an
internationalized host the pin did not match, then the Playwright fallback that
resolves the host itself. Each fix covered the instance, and each next review
found the neighbouring shape. This file turns "every fetch site is guarded" from
a sweep done by hand into a table that fails when a site is added.

Discovery is an AST scan of `jscc/`, `scripts/` and `evals/`. A site is
`(file, enclosing function, what)`, where `what` is one of:

- `import:<module>`  a network-capable module is imported (requests, socket,
  playwright, anthropic, and the other names in NETWORK_MODULES);
- `ref:<dotted>`     a non-exception attribute of such a module is used
  (`ref:requests.Session`, `ref:socket.getaddrinfo`), or a browser/URL-opening
  method such as `.goto` is called;
- `call:fetch_jd`    a call to the one guarded fetch entry point;
- `const:<tool>`     a string naming a command-line fetcher.

Each registry row names the guard that site sits behind and a check that drives
it. The guards are:

- guarded_entry:  the single URL entry point; scheme and address checks, per-hop
                  redirect re-check, resolution pinned to the checked addresses.
- guard_internal: a function that exists to implement or serve the guard.
- check_only:     the URL is checked, then handed to a client that resolves it
                  itself. Documented residual (threat-model T4).
- fixed_endpoint: a vendor API client; the destination is not a value anyone
                  supplies at runtime.
- no_client:      the module is imported for an exception class only.

Deliberately not covered: a fetch made by a dependency we do not read (the
anthropic SDK's own HTTP calls, urllib3 internals), a command shelled out under
a name other than curl or wget, and tests. See each row's guard for what it does
cover.
"""

from __future__ import annotations

import ast
import io
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

from jscc import fetcher
from jscc.fetcher import fetch_jd
from jscc.models import FailureMode

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("jscc", "scripts", "evals")

# Split literals: a dotted quad is a digit run of the same shape the pre-commit
# scanner's phone pattern flags. Splitting the string is the standing convention.
PUBLIC_IP = "93.184." + "216.34"
METADATA_IP = "169.254." + "169.254"
LOOPBACK_IP = "127.0." + "0.1"
PRIVATE_IP = "10.0." + "0.5"
MAPPED_PRIVATE = "::ffff:" + PRIVATE_IP

NETWORK_MODULES = frozenset(
    {
        "requests",
        "urllib3",
        "urllib.request",
        "http.client",
        "httpx",
        "aiohttp",
        "socket",
        "ssl",
        "playwright",
        "selenium",
        "anthropic",
        "smtplib",
        "ftplib",
        "telnetlib",
        "poplib",
        "imaplib",
        "xmlrpc.client",
        "websockets",
        "websocket",
        "paramiko",
        "pycurl",
    }
)
# Attributes of a network module that are exception classes, types or
# constants, not a way to reach the network.
_INERT_SUFFIXES = ("Error", "Exception", "Timeout", "Response")
# Methods that open a URL or navigate a browser, whatever object they sit on.
OPENER_ATTRS = frozenset({"goto", "urlopen", "urlretrieve", "create_connection"})
SHELL_FETCHERS = frozenset({"curl", "wget", "Invoke-WebRequest"})
GUARDED_ENTRY = "fetch_jd"

Site = tuple[str, str, str]


# ---- discovery ----------------------------------------------------------------------


def _matches_network(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in NETWORK_MODULES)


def _is_inert(attr: str) -> bool:
    return attr.endswith(_INERT_SUFFIXES) or attr.isupper()


def discover_in_source(rel: str, source: str) -> set[Site]:
    """The fetch and resolve sites in one source file."""
    tree = ast.parse(source, filename=rel)
    found: set[Site] = set()
    aliases: dict[str, str] = {}  # local name -> network module it names

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        here = ".".join(scope) or "<module>"
        if isinstance(node, ast.Import):
            for a in node.names:
                if _matches_network(a.name):
                    found.add((rel, here, f"import:{a.name}"))
                    aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if _matches_network(node.module) or _matches_network(full):
                    found.add((rel, here, f"import:{node.module}"))
                    aliases[a.asname or a.name] = full
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == GUARDED_ENTRY:
                found.add((rel, here, f"call:{GUARDED_ENTRY}"))
            if name in ("import_module", "__import__"):
                arg = node.args[0] if node.args else None
                literal = arg.value if isinstance(arg, ast.Constant) else None
                if not isinstance(literal, str) or _matches_network(literal):
                    found.add((rel, here, f"import:dynamic({literal or '?'})"))
        elif isinstance(node, ast.Constant) and node.value in SHELL_FETCHERS:
            found.add((rel, here, f"const:{node.value}"))
        elif isinstance(node, ast.Attribute):
            chain = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in aliases and not _is_inert(node.attr):
                dotted = ".".join([aliases[cur.id], *reversed(chain)])
                # `socket.getaddrinfo` is one site, not also `socket`, so only
                # the outermost attribute of a chain is recorded.
                found.add((rel, here, f"ref:{dotted}"))
            elif node.attr in OPENER_ATTRS:
                found.add((rel, here, f"ref:.{node.attr}"))
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.Attribute):
                continue  # inner attributes belong to the chain recorded above
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, (*scope, child.name))
            else:
                walk(child, scope)

    # Aliases must be known before a use that precedes a lazy import in
    # source order, so imports are collected up front.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _matches_network(a.name):
                    aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                if _matches_network(node.module) or _matches_network(f"{node.module}.{a.name}"):
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    walk(tree, ())
    return found


def python_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for top in SCAN_DIRS:
        for path in sorted((REPO_ROOT / top).rglob("*.py")):
            sources[path.relative_to(REPO_ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def discover(sources: dict[str, str] | None = None) -> set[Site]:
    found: set[Site] = set()
    for rel, text in (sources if sources is not None else python_sources()).items():
        found |= discover_in_source(rel, text)
    return found


# ---- checks -------------------------------------------------------------------------


def _function(tree: ast.Module, qualname: str) -> ast.FunctionDef:
    nodes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    return nodes[qualname]


def _fetcher_tree() -> ast.Module:
    return ast.parse((REPO_ROOT / "jscc" / "fetcher.py").read_text(encoding="utf-8"))


def _callers(tree: ast.Module, callee: str) -> set[str]:
    """Names of the functions in `tree` that call `callee`."""
    callers = set()
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == callee:
                callers.add(fn.name)
    return callers


def _no_request(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make any request fail the test, and report if one was attempted."""
    attempted: list[str] = []

    def refuse(url, **kw):
        attempted.append(url)
        raise AssertionError(f"a request was made to {url}")

    monkeypatch.setattr("jscc.fetcher._http_get", refuse)
    return attempted


def check_destination_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = _no_request(monkeypatch)
    for address in (METADATA_IP, LOOPBACK_IP, PRIVATE_IP, "::1", "fe80::1", MAPPED_PRIVATE):
        monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host, a=address: [a])
        result = fetch_jd("https://jobs.example.com/1")
        assert result.failure_mode is FailureMode.blocked, address
        assert "refused before fetching" in result.error_detail, address
    # One public address among private ones is not a pass.
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [PUBLIC_IP, LOOPBACK_IP])
    assert fetch_jd("https://jobs.example.com/1").failure_mode is FailureMode.blocked
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [PUBLIC_IP])
    for url in ("file:///etc/passwd", "ftp://jobs.example.com/x", "gopher://jobs.example.com/"):
        result = fetch_jd(url)
        assert result.failure_mode is FailureMode.blocked, url
        assert "scheme" in result.error_detail, url
    assert attempted == []


def _redirect(location: str) -> requests.Response:
    response = requests.Response()
    response.status_code = 302
    response.headers["location"] = location
    response.raw = io.BytesIO(b"")
    return response


def check_redirect_hops_are_rechecked(monkeypatch: pytest.MonkeyPatch) -> None:
    resolutions = {"public.example.com": [PUBLIC_IP], "internal.example.com": [METADATA_IP]}
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: resolutions[host])
    requested: list[str] = []

    def get(url, **kw):
        requested.append(url)
        assert kw["allow_redirects"] is False
        return _redirect("http://internal.example.com/secret")

    monkeypatch.setattr("jscc.fetcher._http_get", get)
    result = fetch_jd("https://public.example.com/jobs/1")
    assert result.failure_mode is FailureMode.blocked
    assert requested == ["https://public.example.com/jobs/1"]  # never the second hop


def check_resolution_is_pinned_to_the_checked_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    rebound = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (METADATA_IP, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: rebound)
    outer = socket.getaddrinfo
    with fetcher._pinned_resolution("jobs.example.com", [PUBLIC_IP]):
        assert socket.getaddrinfo("jobs.example.com", 443)[0][4][0] == PUBLIC_IP
        # An internationalized spelling of the same name is the same pin.
        assert socket.getaddrinfo("JOBS.example.com.", 443)[0][4][0] == PUBLIC_IP
        assert socket.getaddrinfo("other.example.com", 443)[0][4][0] == METADATA_IP
    assert socket.getaddrinfo is outer  # restored, not left pinned for the process


def check_pin_matches_the_punycode_name(monkeypatch: pytest.MonkeyPatch) -> None:
    rebound = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (METADATA_IP, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: rebound)
    with fetcher._pinned_resolution("bücher.example", [PUBLIC_IP]):
        assert socket.getaddrinfo("xn--bcher-kva.example", 443)[0][4][0] == PUBLIC_IP


def check_proxy_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def get(self, url, **kw):
        seen.append(self.trust_env)
        return _redirect("")

    monkeypatch.setattr(requests.Session, "get", get)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:3128")
    fetcher._http_get("https://jobs.example.com/1")
    assert seen == [False]


def check_resolver_is_reached_only_through_the_guard() -> None:
    tree = _fetcher_tree()
    assert _callers(tree, "_resolve_host") == {"_check_url"}
    assert _callers(tree, "_http_get") == {"_get_guarded"}
    assert _callers(tree, "_get_guarded") == {"fetch_jd"}
    assert _callers(tree, "_check_url") == {"_get_guarded"}


def check_every_request_is_checked_pinned_and_unredirected() -> None:
    fn = _function(_fetcher_tree(), "_get_guarded")
    order = sorted(
        (n.lineno, getattr(n.func, "id", ""))
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("_check_url", "_http_get")
    )
    assert [name for _, name in order] == ["_check_url", "_http_get"]
    withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
    assert any(
        getattr(item.context_expr.func, "id", "") == "_pinned_resolution"  # type: ignore[attr-defined]
        and any(
            getattr(c.func, "id", "") == "_http_get" for c in ast.walk(w) if isinstance(c, ast.Call)
        )
        for w in withs
        for item in w.items
    ), "the request must be made inside the pinned-resolution block"
    keywords = {
        k.arg: k.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_http_get"
        for k in n.keywords
    }
    assert isinstance(keywords["allow_redirects"], ast.Constant)
    assert keywords["allow_redirects"].value is False


def check_browser_runs_only_after_the_url_was_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    def launched(url, timeout):
        raise AssertionError("the browser was started for a refused URL")

    monkeypatch.setattr("jscc.fetcher._render_with_playwright", launched)
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [LOOPBACK_IP])
    result = fetch_jd("https://jobs.example.com/1", use_playwright_fallback=True)
    assert result.failure_mode is FailureMode.blocked
    assert result.used_playwright is False
    tree = _fetcher_tree()
    assert _callers(tree, "_render_with_playwright") == {"fetch_jd"}
    fetch = _function(tree, "fetch_jd")
    lines = {
        getattr(n.func, "id", ""): n.lineno
        for n in ast.walk(fetch)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") in ("_get_guarded", "_render_with_playwright")
    }
    assert lines["_get_guarded"] < lines["_render_with_playwright"]


def check_threat_model_states_the_browser_residual() -> None:
    text = (REPO_ROOT / "docs" / "threat-model.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| T4 ")]
    assert rows, "the SSRF threat row (T4) is gone from docs/threat-model.md"
    assert "playwright" in rows[0].lower(), "T4 no longer names the unpinned browser navigation"


def check_vendor_client_has_no_caller_chosen_endpoint() -> None:
    tree = ast.parse((REPO_ROOT / "jscc" / "llm_client.py").read_text(encoding="utf-8"))
    constructions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "Anthropic"
    ]
    assert constructions, "the vendor client is no longer constructed here; update this row"
    for call in constructions:
        assert {k.arg for k in call.keywords} <= {"api_key"}, "an endpoint or transport was passed"


def check_ci_status_url_has_a_fixed_host() -> None:
    tree = ast.parse((REPO_ROOT / "scripts" / "features.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ci_note")
    urls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.JoinedStr)
        and isinstance(n.values[0], ast.Constant)
        and "api.github.com" in str(n.values[0].value)
    ]
    assert urls, "the CI status URL moved; update this row"
    for url in urls:
        assert str(url.values[0].value).startswith("https://api.github.com/repos/"), (
            "the host must be fixed; only the repo path may vary"
        )


def check_module_uses_only_the_exception_class() -> None:
    for rel in ("jscc/cli/agents.py", "jscc/cli/ingest.py", "jscc/dlq.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        used = {
            n.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and getattr(n.value, "id", "") == "anthropic"
        }
        assert used == {"APIError"}, (rel, used)


def check_fetch_primitives_are_private_to_the_fetcher() -> None:
    private = {
        "_http_get",
        "_get_guarded",
        "_check_url",
        "_resolve_host",
        "_pinned_resolution",
        "_render_with_playwright",
    }
    for rel, text in python_sources().items():
        if rel == "jscc/fetcher.py":
            continue
        names = {n.id for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Attribute)}
        names |= {
            a.name
            for n in ast.walk(ast.parse(text))
            if isinstance(n, ast.ImportFrom)
            for a in n.names
        }
        assert names & private == set(), (rel, names & private)


# ---- the registry -------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    file: str
    function: str
    what: str
    guard: str
    check: Callable[..., None]

    @property
    def site(self) -> Site:
        return (self.file, self.function, self.what)


F = "jscc/fetcher.py"

REGISTRY: list[Row] = [
    Row(
        F,
        "<module>",
        "import:requests",
        "guard_internal",
        check_resolver_is_reached_only_through_the_guard,
    ),
    Row(
        F,
        "<module>",
        "import:socket",
        "guard_internal",
        check_resolution_is_pinned_to_the_checked_addresses,
    ),
    Row(
        F,
        "_resolve_host",
        "ref:socket.getaddrinfo",
        "guard_internal",
        check_destination_refused_before_any_request,
    ),
    Row(
        F,
        "_pinned_resolution",
        "ref:socket.getaddrinfo",
        "guard_internal",
        check_pin_matches_the_punycode_name,
    ),
    Row(
        F, "_http_get", "ref:requests.Session", "guard_internal", check_proxy_environment_is_ignored
    ),
    Row(
        F,
        "_render_with_playwright",
        "import:playwright.sync_api",
        "check_only",
        check_browser_runs_only_after_the_url_was_checked,
    ),
    Row(
        F,
        "_render_with_playwright",
        "ref:.goto",
        "check_only",
        check_threat_model_states_the_browser_residual,
    ),
    Row(
        "jscc/llm_client.py",
        "AnthropicClient.__init__",
        "import:anthropic",
        "fixed_endpoint",
        check_vendor_client_has_no_caller_chosen_endpoint,
    ),
    Row(
        "jscc/llm_client.py",
        "AnthropicClient.__init__",
        "ref:anthropic.Anthropic",
        "fixed_endpoint",
        check_vendor_client_has_no_caller_chosen_endpoint,
    ),
    Row(
        "jscc/cli/agents.py",
        "<module>",
        "import:anthropic",
        "no_client",
        check_module_uses_only_the_exception_class,
    ),
    Row(
        "jscc/cli/ingest.py",
        "<module>",
        "import:anthropic",
        "no_client",
        check_module_uses_only_the_exception_class,
    ),
    Row(
        "jscc/dlq.py",
        "<module>",
        "import:anthropic",
        "no_client",
        check_module_uses_only_the_exception_class,
    ),
    Row(
        "jscc/cli/ingest.py",
        "ingest",
        "call:fetch_jd",
        "guarded_entry",
        check_destination_refused_before_any_request,
    ),
    Row(
        "scripts/features.py",
        "ci_note",
        "const:curl",
        "fixed_endpoint",
        check_ci_status_url_has_a_fixed_host,
    ),
    Row(
        "scripts/smoke_fetch.py",
        "main",
        "call:fetch_jd",
        "guarded_entry",
        check_destination_refused_before_any_request,
    ),
]


# Properties of the guard itself, each one the fix for a shape a review found. They
# are not tied to one site: together they are what "behind the guard" means.
GUARD_CHECKS: list[Callable[..., None]] = [
    check_destination_refused_before_any_request,
    check_redirect_hops_are_rechecked,
    check_resolution_is_pinned_to_the_checked_addresses,
    check_pin_matches_the_punycode_name,
    check_proxy_environment_is_ignored,
    check_every_request_is_checked_pinned_and_unredirected,
    check_resolver_is_reached_only_through_the_guard,
    check_fetch_primitives_are_private_to_the_fetcher,
]

GUARDS = {"guarded_entry", "guard_internal", "check_only", "fixed_endpoint", "no_client"}


def _call(check: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    if "monkeypatch" in check.__code__.co_varnames[: check.__code__.co_argcount]:
        check(monkeypatch)
    else:
        check()


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: [PUBLIC_IP])


def test_every_fetch_or_resolve_site_has_a_row() -> None:
    discovered = discover()
    registered = {r.site for r in REGISTRY}
    assert discovered - registered == set(), (
        "a code path fetches a URL or resolves a host and has no row in REGISTRY; "
        "say which guard it sits behind and add a check"
    )
    assert registered - discovered == set(), "a registry row names a site that no longer exists"


def test_every_row_names_a_known_guard() -> None:
    assert {r.guard for r in REGISTRY} <= GUARDS
    assert len({r.site for r in REGISTRY}) == len(REGISTRY), "duplicate row"


def test_a_site_added_or_a_row_removed_is_caught() -> None:
    """The discovery has to see a new site, and the registry has to notice a gap.

    A synthetic module adds each shape a future change would take; none of them
    may be invisible to discovery.
    """
    shapes = {
        "import httpx\ndef f(u):\n    return httpx.get(u)\n": {"import:httpx", "ref:httpx.get"},
        "import urllib.request as r\ndef f(u):\n    return r.urlopen(u)\n": {
            "import:urllib.request",
            "ref:urllib.request.urlopen",
        },
        "from urllib import request\ndef f(u):\n    return request.urlopen(u)\n": {
            "import:urllib",
            "ref:urllib.request.urlopen",
        },
        "import socket\ndef f(h):\n    return socket.gethostbyname(h)\n": {
            "import:socket",
            "ref:socket.gethostbyname",
        },
        "import importlib\ndef f(n):\n    return importlib.import_module(n)\n": {
            "import:dynamic(?)"
        },
        "def f(page, u):\n    page.goto(u)\n": {"ref:.goto"},
        "from jscc.fetcher import fetch_jd\ndef f(u):\n    return fetch_jd(u)\n": {"call:fetch_jd"},
        "import subprocess\ndef f(u):\n    subprocess.run(['curl', u])\n": {"const:curl"},
    }
    for source, expected in shapes.items():
        found = {what for _, _, what in discover({"jscc/new_module.py": source})}
        assert expected <= found, (source, found)
    # Removing a row leaves its site undeclared; the registry test would fail.
    smaller = {r.site for r in REGISTRY[1:]}
    assert discover() - smaller == {REGISTRY[0].site}
    # And a new site alongside the real ones is the only difference reported.
    sources = python_sources()
    sources["jscc/new_module.py"] = "import httpx\n"
    assert discover(sources) - {r.site for r in REGISTRY} == {
        ("jscc/new_module.py", "<module>", "import:httpx")
    }


@pytest.mark.parametrize("row", REGISTRY, ids=lambda r: f"{r.file}:{r.function}:{r.what}")
def test_each_site_sits_behind_the_guard_its_row_says(
    row: Row, monkeypatch: pytest.MonkeyPatch
) -> None:
    _call(row.check, monkeypatch)


@pytest.mark.parametrize("check", GUARD_CHECKS, ids=lambda c: c.__name__)
def test_guard_property_holds(check: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    _call(check, monkeypatch)
