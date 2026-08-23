#!/usr/bin/env python3
"""Build the Eurskem AI engineering documentation portfolio as PDFs.

Renders a set of professionally formatted PDF volumes into ``docs/pdf/`` by
introspecting the *live* codebase, so every table and entry is provably
derived from the code rather than hand-copied:

  Vol 1  Architecture Overview     curated narrative + screenshots
  Vol 2  Backend Code Reference    AST walk of every app/ module
  Vol 3  Node Type Catalog         NodeRegistry.manifest()
  Vol 4  API Reference             FastAPI route table

Requires WeasyPrint (already a project dependency). Usage:

    python scripts/build_documentation_pdf.py            # build all volumes
    python scripts/build_documentation_pdf.py --only 3   # build one volume
"""
from __future__ import annotations

import argparse
import ast
import html
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "pdf"
SHOTS = ROOT / "portfolio" / "screenshots"
PROJECT = "Eurskem AI"
SUBTITLE = "Agentic Workflow Platform"
TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Shared print CSS
# ---------------------------------------------------------------------------
CSS = """
@page {
    size: A4;
    margin: 20mm 16mm 22mm 16mm;
    @bottom-center { content: counter(page) " / " counter(pages);
        font-size: 8pt; color: #8a8f98; }
    @bottom-right { content: "Eurskem AI"; font-size: 8pt; color: #8a8f98; }
}
@page cover { margin: 0; @bottom-center { content: none; }
    @bottom-right { content: none; } }
* { box-sizing: border-box; }
body { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 10pt;
    line-height: 1.5; color: #1f2430; }
.cover { page: cover; height: 297mm; width: 210mm;
    background: linear-gradient(150deg, #0b1020 0%, #12203f 55%, #0e3a46 100%);
    color: #fff; padding: 40mm 24mm; }
.cover .kicker { letter-spacing: .28em; text-transform: uppercase;
    font-size: 10pt; color: #7fd4c1; margin-bottom: 6mm; }
.cover h1 { font-size: 40pt; line-height: 1.1; margin: 0 0 6mm 0;
    font-weight: 700; }
.cover .sub { font-size: 15pt; color: #c6d2e3; margin-bottom: 16mm; }
.cover .vol { font-size: 12pt; color: #7fd4c1; margin-bottom: 4mm;
    text-transform: uppercase; letter-spacing: .12em; }
.cover .meta { margin-top: 40mm; font-size: 10pt; color: #9fb0c8;
    line-height: 2; }
h1.section { font-size: 20pt; color: #0e3a46; margin: 10mm 0 4mm 0;
    border-bottom: 2px solid #12a08a; padding-bottom: 2mm; }
h2 { font-size: 14pt; color: #12203f; margin: 7mm 0 2mm 0; }
h3 { font-size: 11.5pt; color: #0e3a46; margin: 5mm 0 1.5mm 0; }
p { margin: 0 0 3mm 0; }
code, .code { font-family: "SFMono-Regular", Menlo, Consolas, monospace;
    font-size: 8.6pt; background: #f2f5f8; border-radius: 3px;
    padding: 0.5mm 1.4mm; color: #0e3a46; }
pre { font-family: Menlo, Consolas, monospace; font-size: 8pt;
    background: #0f1522; color: #d7e0ee; padding: 4mm; border-radius: 5px;
    white-space: pre-wrap; line-height: 1.45; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm 0;
    font-size: 8.6pt; }
th { background: #12203f; color: #fff; text-align: left; padding: 2mm 2.4mm;
    font-weight: 600; }
td { border-bottom: 0.3pt solid #dde3ea; padding: 1.8mm 2.4mm;
    vertical-align: top; }
tr:nth-child(even) td { background: #f7f9fb; }
.badge { display: inline-block; font-size: 7.6pt; font-weight: 700;
    color: #fff; background: #12a08a; border-radius: 3px;
    padding: 0.4mm 1.8mm; margin-right: 1mm; }
.badge.ai { background: #6c5ce7; } .badge.det { background: #0984e3; }
.badge.ext { background: #d35400; } .badge.hum { background: #c0392b; }
.badge.io { background: #16a085; }
.muted { color: #6b7280; font-size: 8.6pt; }
.doc { color: #374151; font-size: 8.8pt; }
.sig { font-family: Menlo, Consolas, monospace; font-size: 8.2pt;
    color: #12203f; }
.pagebreak { page-break-before: always; }
.toc a { color: #12203f; text-decoration: none; }
.toc li { margin-bottom: 2mm; }
figure { margin: 4mm 0; text-align: center; }
figure img { max-width: 100%; border: 0.4pt solid #d4dae2;
    border-radius: 4px; }
figcaption { font-size: 8.4pt; color: #6b7280; margin-top: 1.5mm; }
.callout { border-left: 3px solid #12a08a; background: #f0faf7;
    padding: 3mm 4mm; margin: 4mm 0; font-size: 9.2pt; }
"""


def esc(text) -> str:
    return html.escape(str(text) if text is not None else "")


def cover(vol_no: str, title: str, desc: str) -> str:
    return f"""
    <div class="cover">
      <div class="kicker">{esc(PROJECT)} &middot; Engineering Documentation</div>
      <div class="vol">Volume {esc(vol_no)}</div>
      <h1>{esc(title)}</h1>
      <div class="sub">{esc(desc)}</div>
      <div class="meta">
        Platform: {esc(SUBTITLE)}<br/>
        Version: 0.1.0 &nbsp;&middot;&nbsp; Generated: {esc(TODAY)}<br/>
        Built by Ayush Khandelwal<br/>
        Typed workflows compiled to LangGraph &middot; zero-token preflight &middot;
        strict evidence lifecycle
      </div>
    </div>
    """


def render_pdf(html_body: str, out_path: Path) -> None:
    from weasyprint import HTML, CSS as WCSS  # noqa: N812
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/>
    <style>{CSS}</style></head><body>{html_body}</body></html>"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML(string=doc, base_url=str(ROOT)).write_pdf(
        str(out_path), stylesheets=[WCSS(string=CSS)])


# ---------------------------------------------------------------------------
# Volume 3 — Node Type Catalog (from NodeRegistry.manifest())
# ---------------------------------------------------------------------------
def build_vol3() -> None:
    import app.nodes  # noqa: F401 - populate registry via discovery
    from app.nodes.registry import NodeRegistry

    manifest = NodeRegistry.manifest()
    manifest.sort(key=lambda e: (e.get("category", ""), e["type_name"]))

    rows = []
    for e in manifest:
        kind = e.get("execution_kind", "deterministic")
        badge_cls = {"ai": "ai", "deterministic": "det", "external": "ext",
                     "human": "hum", "input": "io",
                     "output": "io"}.get(kind, "det")
        cfg = e.get("config_schema", {}).get("properties", {})
        req = set(e.get("config_schema", {}).get("required", []))
        cfg_names = ", ".join(
            f"<b>{esc(k)}</b>" if k in req else esc(k)
            for k in list(cfg)[:12]) or "&mdash;"
        about = e.get("about", {})
        what = esc(about.get("what") or e.get("description") or "")
        rows.append(f"""<tr>
          <td><code>{esc(e['type_name'])}</code><br/>
              <span class="muted">{esc(e.get('category',''))}</span></td>
          <td><span class="badge {badge_cls}">{esc(kind)}</span>
              {'<span class="badge ai">AI</span>' if e.get('uses_ai') else ''}
              </td>
          <td class="doc">{what}</td>
          <td class="sig">{cfg_names}</td>
        </tr>""")

    by_kind: dict[str, int] = {}
    for e in manifest:
        k = e.get("execution_kind", "deterministic")
        by_kind[k] = by_kind.get(k, 0) + 1
    kind_summary = " &nbsp;&middot;&nbsp; ".join(
        f"{esc(k)}: <b>{v}</b>" for k, v in sorted(by_kind.items()))

    details = []
    for e in manifest:
        about = e.get("about", {})
        cfg = e.get("config_schema", {}).get("properties", {})
        req = set(e.get("config_schema", {}).get("required", []))
        out_fields = ", ".join(
            esc(k) for k in
            e.get("output_schema", {}).get("properties", {})) or "&mdash;"
        in_fields = ", ".join(
            esc(k) for k in
            e.get("input_schema", {}).get("properties", {})) or "&mdash;"
        cfg_rows = []
        for k, meta in cfg.items():
            t = meta.get("type") or ("enum" if "enum" in meta else "any")
            d = esc((meta.get("description") or "").split("\n")[0][:150])
            rq = "<b>required</b>" if k in req else "optional"
            cfg_rows.append(
                f"<tr><td><code>{esc(k)}</code></td><td>{esc(t)}</td>"
                f"<td>{rq}</td><td class='doc'>{d}</td></tr>")
        cfg_table = (
            f"<table><tr><th>Field</th><th>Type</th><th>Req</th>"
            f"<th>Description</th></tr>{''.join(cfg_rows)}</table>"
            if cfg_rows else "<p class='muted'>No configurable fields.</p>")
        details.append(f"""
        <h3 id="{esc(e['type_name'])}"><code>{esc(e['type_name'])}</code>
            <span class="muted">&mdash; {esc(e.get('category',''))}</span></h3>
        <p class="doc">{esc(about.get('what') or e.get('description',''))}</p>
        <p class="muted"><b>Why it exists:</b> {esc(about.get('why') or '—')}
        </p>
        <p class="muted"><b>Receives:</b> {in_fields}<br/>
        <b>Produces:</b> {out_fields}<br/>
        <b>Uses AI:</b> {'Yes' if e.get('uses_ai') else 'No'} &nbsp;
        <b>Acts outside the platform:</b>
            {'Yes' if e.get('external_action') else 'No'}</p>
        {cfg_table}""")

    body = cover("3", "Node Type Catalog",
                 "Every registered workflow node type: its category, "
                 "execution kind, configuration surface, and contract.")
    body += f"""
    <h1 class="section">1. Overview</h1>
    <p>The platform registers <b>{len(manifest)} node types</b> in a single
    registry (<code>app/nodes/registry.py</code>). Every type declares three
    Pydantic schemas &mdash; <code>input_schema</code>,
    <code>output_schema</code>, <code>config_schema</code> &mdash; plus an
    async <code>run()</code>; that is the entire contract. The Builder reads
    this catalog over <code>GET /api/node-types</code>, the runtime compiler
    uses it to instantiate nodes, and preflight validates templates against
    each type's declared outputs. Bold fields in the table are required.</p>
    <p class="callout">Execution kinds: {kind_summary}</p>
    <h1 class="section">2. Master Catalog</h1>
    <table>
      <tr><th style="width:24%">Node type</th><th style="width:14%">Kind</th>
          <th style="width:36%">Purpose</th><th>Config fields</th></tr>
      {''.join(rows)}
    </table>
    <h1 class="section pagebreak">3. Per-Type Reference</h1>
    {''.join(details)}
    """
    render_pdf(body, OUT_DIR / "03_Eurskem_AI_Node_Type_Catalog.pdf")



# ---------------------------------------------------------------------------
# Volume 4 — API Reference (from the live FastAPI app)
# ---------------------------------------------------------------------------
def _auth_level(route) -> str:
    names: list[str] = []

    def walk(dep) -> None:
        call = getattr(dep, "call", None)
        if call is not None:
            names.append(getattr(call, "__name__", ""))
        for sub in getattr(dep, "dependencies", []) or []:
            walk(sub)

    try:
        walk(route.dependant)
    except Exception:
        return "?"
    joined = " ".join(names)
    if "require_admin" in joined:
        return "Admin"
    if "require_consultant" in joined or "require_permission" in joined:
        return "Consultant+"
    if "get_current_user" in joined:
        return "Authenticated"
    return "Public"


def _iter_all_routes(app):
    """Yield concrete routes, unwrapping FastAPI's _IncludedRouter wrappers."""
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from _iter_all_routes(inner)
            continue
        yield route


def build_vol4() -> None:
    from app.main import app

    rows = []
    groups: dict[str, list] = {}
    for route in _iter_all_routes(app):
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        tag = (getattr(route, "tags", None) or ["general"])[0]
        ep = getattr(route, "endpoint", None)
        summary = ((getattr(ep, "__doc__", "") or "").strip()
                   .split("\n")[0][:140])
        groups.setdefault(tag, []).append((sorted(methods), route.path,
                                           summary))
        rows.append((sorted(methods), route.path, _auth_level(route)))

    sections = []
    for tag in sorted(groups):
        trs = []
        for methods, path, summary in groups[tag]:
            m = " ".join(f"<code>{esc(x)}</code>" for x in methods
                         if x != "HEAD")
            trs.append(f"<tr><td>{m}</td><td><code>{esc(path)}</code></td>"
                       f"<td class='doc'>{esc(summary)}</td></tr>")
        sections.append(f"<h2>{esc(tag)}</h2><table>"
                        "<tr><th>Method</th><th>Path</th><th>Summary</th></tr>"
                        f"{''.join(trs)}</table>")

    auth_rows = []
    for methods, path, level in rows:
        m = " ".join(esc(x) for x in methods if x != "HEAD")
        color = {"Public": "#c0392b", "Admin": "#6c5ce7",
                 "Consultant+": "#0984e3",
                 "Authenticated": "#12a08a"}.get(level, "#666")
        auth_rows.append(
            f"<tr><td>{m}</td><td><code>{esc(path)}</code></td>"
            f"<td><span class='badge' style='background:{color}'>"
            f"{esc(level)}</span></td></tr>")

    body = cover("4", "API Reference",
                 "Every HTTP endpoint exposed by the FastAPI application, "
                 "with methods, paths, and authorization level.")
    body += f"""
    <h1 class="section">1. Overview</h1>
    <p>The application mounts <b>{len(rows)} routes</b> across
    <b>{len(groups)}</b> routers. Data routes live under <code>/api/*</code>
    and identity routes under <code>/auth/*</code>; both sit behind Redis
    rate limiting, request-size limits, trusted-host checks, and a strict
    CORS policy wired in <code>app/main.py</code> and
    <code>app/security/middleware.py</code>. Authorization is role-based
    (admin / consultant / viewer) through
    <code>app/security/dependencies.py</code>, and every run-scoped read is
    additionally filtered by the caller's session scope, so one user can
    never observe another user's runs.</p>
    <h1 class="section">2. Authorization Map</h1>
    <table><tr><th>Method</th><th>Path</th><th>Required level</th></tr>
    {''.join(auth_rows)}</table>
    <h1 class="section pagebreak">3. Endpoints by Router</h1>
    {''.join(sections)}
    """
    render_pdf(body, OUT_DIR / "04_Eurskem_AI_API_Reference.pdf")



# ---------------------------------------------------------------------------
# Volume 2 — Backend Code Reference (AST walk of app/)
# ---------------------------------------------------------------------------
def _fmt_sig(node) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    ret = ""
    if getattr(node, "returns", None) is not None:
        try:
            ret = f" -> {ast.unparse(node.returns)}"
        except Exception:
            ret = ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}def {node.name}({args}){ret}"


def _first_doc_line(node) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.strip().split("\n")[0][:160]


def build_vol2() -> None:
    app_root = ROOT / "app"
    packages: dict[str, list[dict]] = {}
    totals = {"modules": 0, "classes": 0, "functions": 0}

    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        pkg = ".".join(rel.parts[1:-1]) or "app"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        totals["modules"] += 1
        loc = len(path.read_text().splitlines())
        classes, functions = [], []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                totals["classes"] += 1
                methods = [n for n in node.body
                           if isinstance(n, (ast.FunctionDef,
                                             ast.AsyncFunctionDef))]
                classes.append((node, methods))
                totals["functions"] += len(methods)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                totals["functions"] += 1
                functions.append(node)
        packages.setdefault(pkg, []).append({
            "path": rel, "tree": tree, "loc": loc,
            "classes": classes, "functions": functions,
        })

    pkg_sections = []
    for pkg in sorted(packages):
        mods_html = []
        for mod in packages[pkg]:
            mod_doc = _first_doc_line(mod["tree"]) or "(no summary)"
            cls_html = []
            for node, methods in mod["classes"]:
                mrows = "".join(
                    f"<div class='sig'>&nbsp;&nbsp;&nbsp;&nbsp;"
                    f"{esc(_fmt_sig(m))}</div>"
                    + (f"<div class='doc' style='margin-left:8mm'>"
                       f"{esc(_first_doc_line(m))}</div>"
                       if _first_doc_line(m) else "")
                    for m in methods if not m.name.startswith("__"))
                cls_html.append(
                    f"<div class='sig'>&#9656; class {esc(node.name)}"
                    f"</div>"
                    + (f"<div class='doc' style='margin-left:4mm'>"
                       f"{esc(_first_doc_line(node))}</div>"
                       if _first_doc_line(node) else "")
                    + mrows)
            fn_html = "".join(
                f"<div class='sig'>&#9656; {esc(_fmt_sig(f))}</div>"
                + (f"<div class='doc' style='margin-left:4mm'>"
                   f"{esc(_first_doc_line(f))}</div>"
                   if _first_doc_line(f) else "")
                for f in mod["functions"])
            mods_html.append(f"""
            <h3><code>{esc(mod['path'])}</code>
                <span class="muted">&mdash; {mod['loc']} lines</span></h3>
            <p class="doc">{esc(mod_doc)}</p>
            {''.join(cls_html)}{''.join(fn_html)}""")
        pkg_sections.append(
            f"<h2 class='pagebreak'>{esc(pkg)} "
            f"<span class='muted'>({len(packages[pkg])} modules)</span></h2>"
            + "".join(mods_html))

    body = cover("2", "Backend Code Reference",
                 "A module-by-module reference for the entire Python "
                 "backend: every file, class, and public function.")
    body += f"""
    <h1 class="section">1. Scope</h1>
    <p>This volume documents the complete backend package
    <code>app/</code> as it exists in source: <b>{totals['modules']}
    modules</b>, <b>{totals['classes']} classes</b>, and
    <b>{totals['functions']} functions/methods</b> across
    <b>{len(packages)} packages</b>. Every entry carries the signature and
    the first line of its docstring, so this volume doubles as a map from
    capability to file. Signatures are generated from the AST; nothing is
    hand-copied.</p>
    <table><tr><th>Package</th><th>Modules</th></tr>
    {''.join(f"<tr><td><code>app.{esc(p) if p != 'app' else ''}</code>"
             f"</td><td>{len(packages[p])}</td></tr>"
             for p in sorted(packages))}
    </table>
    {''.join(pkg_sections)}
    """
    render_pdf(body, OUT_DIR / "02_Eurskem_AI_Backend_Code_Reference.pdf")



# ---------------------------------------------------------------------------
# Volume 1 — Architecture Overview (curated narrative + figures)
# ---------------------------------------------------------------------------
ARCH_SVG = """
<svg viewBox="0 0 760 470" xmlns="http://www.w3.org/2000/svg"
     font-family="Helvetica,Arial" font-size="11">
  <defs>
    <marker id="ar" markerWidth="8" markerHeight="8" refX="7" refY="3"
            orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#55606e"/>
    </marker>
  </defs>
  <rect x="14" y="14" width="732" height="86" rx="8" fill="#eef3f8"
        stroke="#b7c3d0"/>
  <text x="26" y="34" font-weight="bold" fill="#12203f">Product Surfaces
    (React 19 SPA)</text>
  <rect x="30" y="44" width="160" height="42" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="110" y="69" text-anchor="middle">Guided
    Run</text>
  <rect x="204" y="44" width="160" height="42" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="284" y="69" text-anchor="middle">Workflow
    Builder</text>
  <rect x="378" y="44" width="160" height="42" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="458" y="69" text-anchor="middle">Cockpit
    (technical)</text>
  <rect x="552" y="44" width="180" height="42" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="642" y="69" text-anchor="middle">Knowledge /
    Eval / Cost</text>

  <rect x="14" y="122" width="732" height="120" rx="8" fill="#eaf6f2"
        stroke="#9fd8c8"/>
  <text x="26" y="142" font-weight="bold" fill="#0e3a46">FastAPI Application
    (app/)</text>
  <rect x="30" y="152" width="150" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="105" y="176" text-anchor="middle">API
    routers</text><text x="105" y="192" text-anchor="middle"
    fill="#55606e">/api/* /auth/*</text><text x="105" y="208"
    text-anchor="middle" fill="#55606e">SSE + WebSocket</text>
  <rect x="194" y="152" width="150" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="269" y="176" text-anchor="middle">Security
    layer</text><text x="269" y="192" text-anchor="middle"
    fill="#55606e">JWT + RBAC</text><text x="269" y="208"
    text-anchor="middle" fill="#55606e">rate limit, guardrails</text>
  <rect x="358" y="152" width="170" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="443" y="176" text-anchor="middle">Workflow
    runtime</text><text x="443" y="192" text-anchor="middle"
    fill="#55606e">preflight &rarr; compile</text><text x="443" y="208"
    text-anchor="middle" fill="#55606e">LangGraph executor</text>
  <rect x="542" y="152" width="190" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="637" y="176" text-anchor="middle">LLM
    gateway</text><text x="637" y="192" text-anchor="middle"
    fill="#55606e">routing + fallback</text><text x="637" y="208"
    text-anchor="middle" fill="#55606e">cost ledger</text>

  <rect x="14" y="264" width="356" height="120" rx="8" fill="#f3eefb"
        stroke="#c9b8ec"/>
  <text x="26" y="284" font-weight="bold" fill="#3d2e6b">State &amp;
    Storage</text>
  <rect x="30" y="294" width="100" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="80" y="318" text-anchor="middle">MongoDB
    </text><text x="80" y="334" text-anchor="middle" fill="#55606e">runs,
    audit</text>
  <rect x="142" y="294" width="100" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="192" y="318" text-anchor="middle">Redis
    </text><text x="192" y="334" text-anchor="middle"
    fill="#55606e">leases, SSE</text>
  <rect x="254" y="294" width="100" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="304" y="318" text-anchor="middle">Weaviate
    + MinIO</text><text x="304" y="334" text-anchor="middle"
    fill="#55606e">knowledge, files</text>

  <rect x="390" y="264" width="356" height="120" rx="8" fill="#fdf3e7"
        stroke="#ecc89a"/>
  <text x="402" y="284" font-weight="bold" fill="#7a4a12">Sidecars &amp;
    Integrations</text>
  <rect x="406" y="294" width="100" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="456" y="318" text-anchor="middle">MCP
    servers</text><text x="456" y="334" text-anchor="middle"
    fill="#55606e">CRM, F&amp;O, MySQL</text>
  <rect x="518" y="294" width="100" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="568" y="318" text-anchor="middle">OPA +
    Presidio</text><text x="568" y="334" text-anchor="middle"
    fill="#55606e">policy, PII</text>
  <rect x="630" y="294" width="100" height="76" rx="6" fill="#fff"
        stroke="#8fa2b5"/><text x="680" y="318" text-anchor="middle">Snippet
    runner</text><text x="680" y="334" text-anchor="middle"
    fill="#55606e">isolated Python</text>

  <rect x="14" y="404" width="732" height="52" rx="8" fill="#f0f4f8"
        stroke="#b7c3d0"/>
  <text x="26" y="424" font-weight="bold" fill="#12203f">Observability</text>
  <text x="26" y="442" fill="#55606e">structlog JSON &middot; Prometheus
    metrics &middot; Grafana dashboards &middot; OTel tracing &middot; Mongo
    audit events</text>

  <line x1="110" y1="100" x2="105" y2="152" stroke="#55606e"
        marker-end="url(#ar)"/>
  <line x1="443" y1="228" x2="304" y2="294" stroke="#55606e"
        marker-end="url(#ar)"/>
  <line x1="500" y1="190" x2="542" y2="190" stroke="#55606e"
        marker-end="url(#ar)"/>
  <line x1="637" y1="228" x2="600" y2="294" stroke="#55606e"
        marker-end="url(#ar)"/>
</svg>
"""



def build_vol1() -> None:
    def shot(name: str, caption: str, width: str = "92%") -> str:
        p = SHOTS / name
        if not p.exists():
            return ""
        return (f"<figure><img src='{p.relative_to(ROOT)}' "
                f"style='width:{width}'/>"
                f"<figcaption>{esc(caption)}</figcaption></figure>")

    body = cover("1", "Architecture Overview",
                 "How the platform is built: one typed workflow runtime, "
                 "five canonical Workflow surfaces, and the infrastructure beneath.")
    body += f"""
    <h1 class="section">1. What the platform is</h1>
    <p><b>{esc(PROJECT)}</b> is an agentic workflow platform for
    high-stakes work. A validated YAML contract compiles to a LangGraph
    state graph; every node passes through one runtime boundary that binds
    cost context, publishes lifecycle events, writes audit records, checks
    for cooperative pause, resolves configuration templates, validates
    output against a schema, and checkpoints for recovery. The result is
    visible automation with typed steps, explicit evidence, bounded
    provider use, and human control at material decisions.</p>
    <div class="callout">Five product surfaces share one Workflow model:
    <b>Chat</b>, <b>Workflows</b>, <b>Builder</b>, <b>Cockpit</b>, and
    <b>Run History</b>. Workflow identity, run ID, node outputs, approvals and
    durable history remain consistent across them.</div>

    <h1 class="section">2. System Architecture</h1>
    <p>Figure 1 shows the four layers: the React SPA surfaces, the FastAPI
    application (API, security, workflow runtime, LLM gateway), the state
    and storage tier, and the sidecar/integration tier, with observability
    spanning all of them.</p>
    <figure>{ARCH_SVG}<figcaption>Figure 1 &mdash; System architecture.
    </figcaption></figure>

    <h1 class="section pagebreak">3. Workflow surfaces</h1>
    <h2>3.1 Chat &amp; Workflows</h2>
    <p>Workflows are presented by outcome, not by graph shape. Chat can launch
    conversation-scoped runs, while Workflows provides canonical discovery and
    launch into Cockpit.</p>
    {shot('01-workflow-library.png', 'The Workflow Library.')}

    <h2 class="pagebreak">3.2 Workflow Builder</h2>
    <p>A stable four-area layout: action bar, registry-driven node library,
    canvas, and persistent inspector. Typed configuration forms, visual
    data mapping, node/branch tests, autosave drafts, and immutable
    versions.</p>
    {shot('09-builder-canvas-palette.png', 'Builder canvas and node '
          'palette.')}
    {shot('11-builder-configure.png', 'Typed configuration in the '
          'inspector.')}
    {shot('14-builder-map-data.png', 'Visual data mapping between nodes.')}
    {shot('12-builder-preflight.png', 'Zero-token preflight in the '
          'Builder.')}

    <h2 class="pagebreak">3.3 Cockpit</h2>
    <p>Live graph and node lifecycle, events, failure diagnosis, audit and
    output inspection for technical review.</p>
    {shot('06-cockpit-graph.png', 'Cockpit live graph.')}
    {shot('07-cockpit-node-detail.png', 'Node lifecycle detail.')}
    {shot('20-cockpit-audit.png', 'Audit trail.')}
    """
    body += _vol1_part2(shot)
    render_pdf(body, OUT_DIR / "01_Eurskem_AI_Architecture_Overview.pdf")



def _vol1_part2(shot) -> str:
    return f"""
    <h1 class="section pagebreak">4. How a workflow runs</h1>
    <p>The lifecycle of a single run is the backbone of the system:</p>
    <table>
      <tr><th style="width:22%">Stage</th><th>What happens</th></tr>
      <tr><td><b>Author</b></td><td class="doc">A YAML contract is written
        in the Builder or imported; the schema loader validates structure.
        </td></tr>
      <tr><td><b>Preflight</b></td><td class="doc">A zero-token static pass
        checks topology, templates, model prerequisites and service
        availability &mdash; before any provider call is made.</td></tr>
      <tr><td><b>Launch</b></td><td class="doc">A durable run record and
        checkpoint are created, then execution is detached under a Redis
        run-ownership lease so a duplicate launch is a no-op.</td></tr>
      <tr><td><b>Execute</b></td><td class="doc">The compiled LangGraph
        runs; every node passes through the runtime boundary (events, cost,
        audit, validation, checkpoint).</td></tr>
      <tr><td><b>Pause</b></td><td class="doc">Human-in-the-loop gates
        interrupt durably; state is persisted so resume works from any
        worker.</td></tr>
      <tr><td><b>Resume</b></td><td class="doc">A decision rebuilds from
        the Redis checkpointer, replays completed nodes without re-calling
        providers, and continues.</td></tr>
      <tr><td><b>Finalize</b></td><td class="doc">The terminal state is
        persisted, the projected output is computed, and pipeline/subprocess
        callbacks reconcile.</td></tr>
    </table>
    {shot('18-run-detail-timeline.png', 'Run timeline in Run History.')}

    <h1 class="section">5. Evidence integrity &amp; cost</h1>
    <p>Every call through the LLM gateway is written to the cost ledger
    with run ID, node ID, tokens, and both the intended and the actual
    model &mdash; because a fallback may execute on provider failure, and
    that substitution is a recorded fact rather than an invisible one.
    Claims are verified against sources, and the evidence lifecycle keeps
    provenance explicit.</p>
    {shot('25-evidence-verified-claims.png', 'Verified claims with '
          'evidence.')}
    {shot('21-cockpit-score.png', 'Cost and scoring in the Cockpit.')}

    <h1 class="section">6. Deployment &amp; operations</h1>
    <p>Production runs on a single IONOS VPS behind Caddy (automatic TLS):
    an immutable release archive is transferred over SSH, validated with
    <code>sha256sum</code>, built with Docker Compose, health-checked
    through <code>/ready</code>, and automatically rolled back to the
    previous release if readiness fails. A post-deploy smoke/load test
    confirms the deployment before the <code>current</code> symlink moves.
    Backups capture Mongo, MinIO objects, Weaviate and Redis volumes with
    checksums. Containers run hardened: pinned images, read-only root
    filesystems, <code>cap_drop: ALL</code>, <code>no-new-privileges</code>,
    non-root users, and least-privilege service credentials.</p>
    """

# ---------------------------------------------------------------------------
# Volume 5 — Node Type Engineering Standard & Known Issues
# ---------------------------------------------------------------------------
def build_vol5() -> None:
    body = cover("5", "Node Type Engineering Standard",
                 "Why adding node types produces template and input/output "
                 "errors, the contract every node type must satisfy, and "
                 "the known-issues register with the remediation roadmap.")
    body += """
    <h1 class="section">1. The problem</h1>
    <p>Adding a new node type has historically surfaced errors from two
    places: <b>template resolution</b> (downstream
    <code>{{node.field}}</code> references rejected by preflight) and
    <b>input/output mismatch</b> (configs or outputs that do not line up
    with what preflight, the Builder, and the generation LLM expect).
    Neither is a bug in the new node itself &mdash; both are the
    consequence of a node type being consumed by <b>six different
    systems</b>, only some of which update automatically when the type is
    registered.</p>

    <h1 class="section">2. Anatomy: the six consumers of a node type</h1>
    <table>
      <tr><th style="width:4%">#</th><th style="width:26%">Consumer</th>
          <th style="width:34%">Mechanism</th><th>Auto-updates?</th></tr>
      <tr><td>1</td><td>Runtime compiler</td>
          <td class="doc"><code>discover_nodes()</code> imports every module
          in <code>app/nodes</code> and collects
          <code>@NodeRegistry.register</code> classes.</td>
          <td><span class="badge det">Yes</span></td></tr>
      <tr><td>2</td><td>Builder palette &amp; inspector</td>
          <td class="doc"><code>NodeRegistry.manifest()</code> serves
          <code>GET /api/node-types</code>: config/input/output JSON
          schemas plus category, icon, family, execution kind and About.
          </td><td><span class="badge det">Yes</span></td></tr>
      <tr><td>3</td><td>Preflight template checks</td>
          <td class="doc">Validates <code>{{node.field}}</code> references
          against <code>preflight_output_fields()</code>. The default
          exposes only static <code>output_schema</code> fields;
          <b>dynamic outputs need a per-node override or a typed-output
          builder in <code>logic_preflight.py</code></b>.</td>
          <td><span class="badge ext">Conditional</span> &mdash; this is
          where TEMPLATE_UNKNOWN_OUTPUT_FIELD comes from</td></tr>
      <tr><td>4</td><td>Preflight coverage gate</td>
          <td class="doc"><code>tests/test_node_preflight_coverage.py</code>
          requires every new type to be acknowledged with a review note.
          </td><td><span class="badge hum">Manual by design</span></td></tr>
      <tr><td>5</td><td>Generation &amp; autofix prompts</td>
          <td class="doc">The LLM catalog is built from the manifest, and
          the capability shortlist scores registry text &mdash; both
          auto-update. <b>But</b> the real-usage example embedded in the
          prompt is mined from workflows on disk
          (<code>about_synthesis.example_workflow_path</code>). A type used
          by no workflow ships with no example, so the model guesses config
          shapes.</td>
          <td><span class="badge ext">Conditional</span> &mdash; needs at
          least one reference workflow</td></tr>
      <tr><td>6</td><td>Palette presentation tables</td>
          <td class="doc"><code>categories.py</code> lookup tables and UI
          palette labels; sane defaults apply.</td>
          <td><span class="badge det">Yes (defaults)</span></td></tr>
    </table>
    <div class="callout">Conclusion: a new node type is only truly
    &ldquo;wired in&rdquo; when (a) its dynamic output fields are declared
    to preflight, and (b) at least one known-good workflow on disk
    exercises it, giving generation and autofix a real exemplar.</div>
    """
    body += _vol5_part2()
    render_pdf(body, OUT_DIR / "05_Eurskem_AI_Node_Type_Standard.pdf")

def _vol5_part2() -> str:
    return _vol5_sections_3_4() + _vol5_sections_5_7()


def _vol5_sections_3_4() -> str:
    return """
    <h1 class="section pagebreak">3. The node type contract (the standard)
    </h1>
    <p>Every node type is one module in <code>app/nodes/</code> containing
    one <code>@NodeRegistry.register</code> class. The complete surface is
    three Pydantic schemas and one async method:</p>
    <table>
      <tr><th style="width:26%">Declaration</th><th>Purpose</th>
          <th style="width:30%">Rule</th></tr>
      <tr><td><code>type_name</code></td><td class="doc">Registry key and
          YAML <code>type:</code> value.</td><td class="doc">PascalCase,
          unique, stable forever.</td></tr>
      <tr><td><code>config_schema</code></td><td class="doc">YAML config
          shape, validated at compile time.</td><td class="doc">Must set
          <code>extra="forbid"</code>; every field needs a description
          (feeds the Builder form and the LLM catalog).</td></tr>
      <tr><td><code>input_schema</code></td><td class="doc">What the node
          reads from state.</td><td class="doc">Keep minimal; document
          optional inputs in field descriptions.</td></tr>
      <tr><td><code>output_schema</code></td><td class="doc">What the node
          writes to <code>node_outputs[node_id]</code>; the compiler
          validates every run output against it.</td>
          <td class="doc">If outputs are config-dependent or open-shaped,
          override <code>preflight_output_fields()</code>.</td></tr>
      <tr><td><code>run(state, resolved_config)</code></td>
          <td class="doc">Async execution; returns a dict conforming to
          <code>output_schema</code>.</td><td class="doc">Never call
          providers without finite timeouts; never swallow errors silently.
          </td></tr>
      <tr><td><code>required_services(config)</code></td>
          <td class="doc">Services needed at runtime (e.g.
          <code>llm</code>, <code>email</code>).</td>
          <td class="doc">Preflight aggregates these into the workflow's
          service-availability check.</td></tr>
      <tr><td><code>preflight_output_fields(config)</code></td>
          <td class="doc">Valid dotted template-reference suffixes beyond
          the static schema.</td><td class="doc">Entries may be exact
          fields or dotted prefixes (<code>data.*</code>).</td></tr>
      <tr><td><code>about</code> / <code>family</code> /
          <code>execution_kind</code></td><td class="doc">Author-facing
          About tab, palette grouping, automation-boundary badge.</td>
          <td class="doc">Optional; <code>about_synthesis</code> fills
          gaps from schemas and real workflow adjacency.</td></tr>
    </table>

    <h1 class="section">4. Template resolution rules</h1>
    <p>Templates are a restricted dotted-path resolver
    (<code>app/runtime/templating.py</code>), not a general template
    engine: <code>{{inputs.x}}</code>, <code>{{variables.x}}</code>,
    <code>{{outputs.node_id.field.path}}</code>, optional trailing
    <code>?</code>, numeric list indices. Preflight enforces these codes —
    each maps to a specific authoring mistake:</p>
    <table>
      <tr><th style="width:34%">Code</th><th>Meaning</th></tr>
      <tr><td><code>TEMPLATE_UNKNOWN_NODE</code></td><td class="doc">
          Reference names a node that does not exist upstream.</td></tr>
      <tr><td><code>TEMPLATE_UNKNOWN_OUTPUT_FIELD</code></td>
          <td class="doc">Field path not covered by the referenced node's
          <code>preflight_output_fields()</code> — the most common error
          when a new node type has dynamic outputs.</td></tr>
      <tr><td><code>TEMPLATE_NOT_UPSTREAM</code></td><td class="doc">
          Reference reaches for a node that is not upstream of the current
          one (would be a runtime race).</td></tr>
      <tr><td><code>TEMPLATE_NULLABLE_NESTED_ACCESS</code></td>
          <td class="doc">Path traverses a field whose declared type
          permits <code>None</code>; would crash mid-run.</td></tr>
      <tr><td><code>TEMPLATE_STATICALLY_EMPTY_FIELD</code></td>
          <td class="doc">Reference can only ever substitute a
          statically-known-empty value.</td></tr>
      <tr><td><code>TEMPLATE_UNKNOWN_INPUT</code> /
          <code>TEMPLATE_UNKNOWN_VARIABLE</code> /
          <code>TEMPLATE_UNKNOWN_STRUCTURED_FIELD</code></td>
          <td class="doc">Reference to an undeclared input, variable, or
          structured-output field.</td></tr>
      <tr><td><code>TEMPLATE_SELF_REFERENCE</code> /
          <code>TEMPLATE_UNBALANCED</code> /
          <code>TEMPLATE_CONDITIONAL_UPSTREAM</code></td>
          <td class="doc">Self reference; malformed braces; reference to a
          node that only executes on another branch.</td></tr>
    </table>
    """



def _vol5_sections_5_7() -> str:
    return _vol5_sections_5_6() + """
    <h1 class="section pagebreak">7. Checklist: adding a node type today
    </h1>
    <table>
      <tr><th style="width:5%">#</th><th>Step</th></tr>
      <tr><td>1</td><td class="doc">Create
          <code>app/nodes/&lt;name&gt;.py</code> with the
          <code>@NodeRegistry.register</code> class; declare
          <code>type_name</code>, three schemas
          (<code>extra="forbid"</code> on config), and
          <code>run()</code>.</td></tr>
      <tr><td>2</td><td class="doc">Declare
          <code>required_services()</code>; override
          <code>preflight_output_fields()</code> for any dynamic output
          shape (dotted prefixes allowed).</td></tr>
      <tr><td>3</td><td class="doc">Add the review record in
          <code>tests/test_node_preflight_coverage.py</code> documenting
          which extension points were reviewed.</td></tr>
      <tr><td>4</td><td class="doc">Write unit tests with
          <code>StubLLM</code>/fake services proving the output validates
          against <code>output_schema</code>.</td></tr>
      <tr><td>5</td><td class="doc">Add at least one workflow using the
          node (reference corpus after R2) so generation and autofix get a
          real exemplar, then run
          <code>scripts/preflight_workflows.py --warnings-as-errors</code>.
          </td></tr>
      <tr><td>6</td><td class="doc">Run the full backend test suite; the
          Builder palette and About tab pick the type up automatically via
          the manifest.</td></tr>
    </table>
    """



def _vol5_sections_5_6() -> str:
    return _vol5_issue_register() + _vol5_roadmap()


def _vol5_issue_register() -> str:
    return """
    <h1 class="section pagebreak">5. Known issues register (current)</h1>
    <table>
      <tr><th style="width:5%">ID</th><th style="width:38%">Issue</th>
          <th style="width:10%">Severity</th><th>Remediation</th></tr>
      <tr><td>NT-1</td><td class="doc">Dynamic-output node types without a
          <code>preflight_output_fields()</code> override produce
          TEMPLATE_UNKNOWN_OUTPUT_FIELD for valid references.</td>
          <td><span class="badge ext">High</span></td><td class="doc">
          Contract &sect;3 makes the override mandatory for open-shaped
          outputs; the conformance harness detects untyped dynamic outputs
          automatically.</td></tr>
      <tr><td>NT-2</td><td class="doc">New types appear in no workflow, so
          generation/autofix prompts carry no real-usage example and the
          LLM guesses config shapes.</td>
          <td><span class="badge ext">High</span></td><td class="doc">
          Reference-workflow corpus (roadmap R2) gives every type curated
          exemplars.</td></tr>
      <tr><td>NT-3</td><td class="doc">The
          <code>ACKNOWLEDGED_NODE_TYPES</code> snapshot fails on every new
          type with an opaque diff.</td><td><span class="badge io">Medium
          </span></td><td class="doc">Replaced by a per-type review record
          plus executable conformance checks.</td></tr>
      <tr><td>NT-4</td><td class="doc">Builder round-trip writes derived
          Start-node <code>inputs</code> back into saved YAML, mutating
          shipped workflows.</td><td><span class="badge io">Medium</span>
          </td><td class="doc">Fix in <code>yaml-bridge.ts</code>: emit
          only explicit inputs; the round-trip test enforces zero drift.
          </td></tr>
      <tr><td>NT-5</td><td class="doc">Four shipped workflows fail
          <code>--warnings-as-errors</code> (nullable nested access and
          required-nullable extraction fields).</td>
          <td><span class="badge io">Medium</span></td><td class="doc">
          Per-workflow YAML fixes or explicit waivers.</td></tr>
      <tr><td>NT-6</td><td class="doc">No executable per-type conformance
          battery exists today; mistakes surface only during authoring.
          </td><td><span class="badge io">Medium</span></td>
          <td class="doc">Conformance harness (roadmap R1).</td></tr>
    </table>
    """



def _vol5_roadmap() -> str:
    return """
    <h1 class="section">6. Remediation roadmap</h1>
    <table>
      <tr><th style="width:8%">Step</th><th style="width:40%">Deliverable
          </th><th>Effect</th></tr>
      <tr><td>R1</td><td class="doc">Node conformance harness:
          auto-parametrized tests over the whole registry (config
          strictness, manifest completeness, minimal-workflow compile +
          preflight, template matrix per declared output field,
          required-services validity, stub-executed output validation).
          </td><td class="doc">A new type is green only when the whole
          contract holds; failures become actionable instead of being
          discovered mid-authoring.</td></tr>
      <tr><td>R2</td><td class="doc">Hidden reference corpus
          <code>workflows/reference/&lt;NodeType&gt;/*.yaml</code>:
          machine-gated example workflows (at least 7 per type, 400+
          total), invisible in the UI library, scanned by
          <code>about_synthesis</code>, preflight CI, and generation
          exemplar selection.</td><td class="doc">Every type gains curated
          exemplars; preflight gains a regression net; autofix and
          generation update automatically forever.</td></tr>
      <tr><td>R3</td><td class="doc">Autofix/generation wiring: embed one
          or two reference snippets per shortlisted type in generate and
          repair prompts; regression test proving a freshly registered
          type needs zero manual edits anywhere.</td><td class="doc">The
          &ldquo;autofix updates automatically&rdquo; guarantee becomes a
          test.</td></tr>
      <tr><td>R4</td><td class="doc">Node Type Studio (follow-up):
          UI-defined node types stored as declarative definitions
          (FieldSpec config + output schemas, prompt recipe), materialized
          into the registry behind the same contract, published only after
          passing R1 checks.</td><td class="doc">Node types can be added
          from the UI without code changes.</td></tr>
    </table>
    """

# ---------------------------------------------------------------------------
# Volume 6 — Production Readiness Assessment
# ---------------------------------------------------------------------------
def _pill(color: str, label: str) -> str:
    return (f"<span class='badge' style='background:{color};padding:"
            f"1mm 3mm;font-size:8.4pt'>{label}</span>")


def _pr(status: str) -> str:
    return {"G": _pill("#1e8e3e", "GREEN"),
            "Y": _pill("#b06000", "YELLOW"),
            "R": _pill("#c5221f", "RED")}[status]


def build_vol6() -> None:
    body = cover("6", "Production Readiness Assessment",
                 "A measured, evidence-based verdict on whether the "
                 "platform is ready for production, with a prioritized "
                 "issue register and a staged remediation roadmap.")
    body += _vol6_verdict() + _vol6_scorecard()
    body += _vol6_findings() + _vol6_roadmap_checklist()
    render_pdf(body, OUT_DIR /
               "06_Eurskem_AI_Production_Readiness_Assessment.pdf")


def _vol6_verdict() -> str:
    return """
    <h1 class="section">1. Verdict</h1>
    <p style="font-size:13pt"><b>READY FOR PRODUCTION AFTER SPECIFIED
    FIXES.</b></p>
    <p>The platform's security architecture, durability design (leases,
    durable checkpoints, idempotency ledgers, single-use tokens),
    deployment pipeline (immutable releases, automatic rollback, backups),
    and observability foundation are demonstrably production-grade. What
    blocks a clean launch today is a small, well-scoped set of regressions
    and gaps concentrated in the most recent work: a red CI at HEAD, one
    SSRF gap in the External Action node, one unimplemented MCP tool
    handler, and a shared-subprocess schema regression. None requires
    architectural change.</p>
    <div class="callout"><b>Basis.</b> The assessment combines a full
    static audit with live measurements taken in this environment: the
    backend suite (1,918 passing / 11 failing), the zero-token preflight
    gate, the frontend type/lint/unit checks, secret-in-git scans, and a
    dependency review.</div>

    <h1 class="section">2. Baseline results (measured)</h1>
    <table>
      <tr><th style="width:34%">Check</th><th style="width:30%">Result
          </th><th>Classification</th></tr>
      <tr><td>Backend import &amp; test collection</td>
          <td>OK &middot; 1,932 collected</td><td class="doc">&mdash;</td>
          </tr>
      <tr><td>Backend pytest</td>
          <td>1,918 pass &middot; 11 fail &middot; 3 skip</td>
          <td class="doc">Regressions from recent commits</td></tr>
      <tr><td>Workflow preflight
          (<code>--warnings-as-errors</code>)</td>
          <td>4 workflows FAIL</td>
          <td class="doc">Nullable / required-null fields</td></tr>
      <tr><td>Frontend unit tests (vitest)</td>
          <td>332 pass &middot; 9 fail</td>
          <td class="doc">Builder YAML round-trip drift</td></tr>
      <tr><td>Frontend ESLint (<code>--max-warnings=0</code>)</td>
          <td>26 errors</td><td class="doc">Hooks / fast-refresh</td></tr>
      <tr><td>Frontend type check</td><td>4 errors</td>
          <td class="doc">New test file + unused import</td></tr>
      <tr><td>Secrets in git</td>
          <td>None (only <code>.env.example</code> tracked)</td>
          <td class="doc">OK</td></tr>
      <tr><td>Dependency freshness</td>
          <td>Current (jose 3.5, fastapi 0.140, httpx 0.28)</td>
          <td class="doc">OK</td></tr>
      <tr><td>CI at HEAD</td><td>All jobs would fail</td>
          <td class="doc">Deployment blocked</td></tr>
    </table>
    """



def _vol6_scorecard() -> str:
    rows = [
        ("Correctness", "Y", "11 failing tests incl. one live workflow "
         "regression (sp01&rarr;w03) and one unimplemented MCP tool; all "
         "fixable and understood."),
        ("Security", "Y", "Excellent baseline controls; one High SSRF gap "
         "in External Action, one Medium session-revocation gap."),
        ("Architecture", "G", "Single runtime boundary, durable HITL, "
         "worker-safe coordination; no rebuild needed."),
        ("Database", "G", "Versioned lease-locked migrations, indexes, "
         "session scoping; one config-drift trap (DB_NAME)."),
        ("Performance", "G", "Finite timeouts everywhere, caching, no "
         "unbounded queries found; minor sync-insert note."),
        ("Reliability", "G", "Leases, replay, fallback chains, "
         "ambiguous-write handling."),
        ("Testing", "Y", "Deep suite (1,932) but red at HEAD; frontend "
         "tests not gated in CI."),
        ("Observability", "Y", "Strong logging/metrics/audit; no alert "
         "rules yet."),
        ("Deployment", "R", "Pipeline design is excellent but cannot run "
         "because CI is red at HEAD (transient)."),
        ("Maintainability", "G", "Handbook docs, 4 TODOs in 80k LOC, "
         "consistent conventions."),
    ]
    trs = "".join(
        f"<tr><td><b>{area}</b></td><td>{_pr(st)}</td>"
        f"<td class='doc'>{ev}</td></tr>" for area, st, ev in rows)
    return f"""
    <h1 class="section pagebreak">3. Feasibility scorecard</h1>
    <table>
      <tr><th style="width:20%">Area</th><th style="width:14%">Rating</th>
          <th>Evidence</th></tr>
      {trs}
    </table>
    <p class="muted">Green = acceptable for production &middot; Yellow =
    improvement required but manageable &middot; Red = production blocker.
    </p>
    """



def _vol6_findings() -> str:
    high = [
        ("REL-1", "CI red at HEAD", "Deployment",
         "11 backend failures, 4 preflight fails, 26 lint errors, 4 tsc "
         "errors; CD gated on CI so nothing can ship."),
        ("SEC-1", "SSRF in ExternalActionService", "Security",
         "No URL scheme/host validation; template-resolved URLs can reach "
         "internal services and cloud metadata."),
        ("COR-1", "Unimplemented MCP tool handler", "Correctness",
         "get_quotations_for_account is declared but has no handler; "
         "runtime error when called."),
        ("COR-2", "Shared sp01 schema change breaks w03", "Correctness",
         "3 end-to-end tests red; a shipped workflow's subprocess fails "
         "validation."),
    ]
    med = [
        ("COR-3", "Candidates API shape regression", "TypeError / {}-vs-[]"
         " mismatch in discovered-candidates handling."),
        ("COR-4", "Builder YAML round-trip drift", "Derived Start inputs "
         "written back into saved YAML (9 frontend tests red)."),
        ("COR-5", "Stale tests reference deleted workflow", "3 modules "
         "point at removed horizon_proposal_hitl_pdf.yaml."),
        ("DB-1", "DB_NAME divergence trap", "Hardcoded DB_NAME vs "
         "settings.mongo_db can split-brain if MONGO_DB changes."),
        ("SEC-2", "JWT role in token, no revocation", "10h tokens; a "
         "deactivated user keeps access until expiry."),
        ("OBS-1", "No alert rules", "Metrics exist but nothing pages "
         "anyone."),
        ("REL-2", "No global frontend 401 handling", "Expired cookie "
         "surfaces as generic errors."),
        ("DEV-2", "vitest not gated in CI", "How the Builder drift "
         "shipped."),
    ]
    low = [
        ("DEP-1", "argon2 imported but undeclared; passlib declared but "
         "unused"),
        ("CLN-1", "Committed junk: _mounttest.txt, orphan ui/index.css, "
         "uiw/, root npm artifacts"),
        ("SEC-3", "Rate limiter fails closed (503) when Redis is down "
         "(documented tradeoff)"),
        ("REL-4", "Synchronous cost-ledger insert on the event loop "
         "(low volume)"),
        ("DEV-3", "Unpinned redis-stack-server:latest in the dev compose"),
    ]
    high_rows = "".join(
        f"<tr><td><code>{i}</code></td><td><b>{t}</b></td>"
        f"<td class='doc'>{c}</td><td class='doc'>{d}</td></tr>"
        for i, t, c, d in high)
    med_rows = "".join(
        f"<tr><td><code>{i}</code></td><td class='doc'>{t}</td>"
        f"<td class='doc'>{d}</td></tr>" for i, t, d in med)
    low_rows = "".join(f"<tr><td><code>{i}</code></td>"
                       f"<td class='doc'>{t}</td></tr>" for i, t in low)
    return f"""
    <h1 class="section pagebreak">4. High-severity findings</h1>
    <table>
      <tr><th style="width:8%">ID</th><th style="width:26%">Title</th>
          <th style="width:14%">Category</th><th>Production impact</th></tr>
      {high_rows}
    </table>
    <h1 class="section">5. Medium-severity findings</h1>
    <table>
      <tr><th style="width:10%">ID</th><th style="width:36%">Title</th>
          <th>Detail</th></tr>
      {med_rows}
    </table>
    <h1 class="section">6. Low-severity findings</h1>
    <table>
      <tr><th style="width:10%">ID</th><th>Title</th></tr>
      {low_rows}
    </table>
    <div class="callout">No Critical findings: no confirmed data-loss,
    breach, or corruption path exists in the current code.</div>
    """



def _vol6_roadmap_checklist() -> str:
    stages = [
        ("Stage 0", "Protect the baseline",
         "Freeze current build/test status as the reference point; keep "
         "the repo runnable after every step."),
        ("Stage 1", "Production blockers",
         "Fix the 11 backend failures and 4 preflight fails (TASK-1), add "
         "SSRF protection to External Action (TASK-2), restore the "
         "frontend gates and gate vitest in CI (TASK-3)."),
        ("Stage 2", "Correctness & reliability",
         "Remove the DB_NAME divergence trap, add frontend session-expiry "
         "handling, lock the candidates API contract."),
        ("Stage 3", "Code cleanup",
         "Dependency hygiene (argon2/passlib), delete committed junk, pin "
         "the dev redis image."),
        ("Stage 4", "Architecture",
         "None recommended now; the residual in-memory fast paths are "
         "documented and Redis-dependent, not a scaling blocker."),
        ("Stage 5", "Performance",
         "Optional: async cost-ledger writes if LLM volume grows."),
        ("Stage 6", "Test hardening",
         "SSRF regression tests; explicit cross-user authorization tests."),
        ("Stage 7", "Operational readiness",
         "Alert rules, restore drill, session-policy decision, backup "
         "heartbeat."),
        ("Stage 8", "Production validation",
         "Re-run this entire battery plus staging smoke, backup/restore "
         "and rollback drills."),
    ]
    stage_rows = "".join(
        f"<tr><td><b>{s}</b></td><td><b>{t}</b></td>"
        f"<td class='doc'>{d}</td></tr>" for s, t, d in stages)

    def item(done: bool, text: str) -> str:
        mark = "&#9745;" if done else "&#9744;"
        return (f"<tr><td style='width:6%;font-size:12pt'>{mark}</td>"
                f"<td class='doc'>{text}</td></tr>")

    cl = [
        (True, "Production build passes (Docker image, compose config)"),
        (False, "Static/type checks pass &mdash; blocked by TASK-3"),
        (False, "Lint passes &mdash; blocked by TASK-3"),
        (False, "Critical tests pass &mdash; blocked by TASK-1"),
        (False, "Core end-to-end workflows pass &mdash; blocked by TASK-1"),
        (True, "Authentication verified (Argon2, JWT claim validation)"),
        (True, "Authorization verified (RBAC + session scoping)"),
        (True, "Resource ownership verified"),
        (True, "Secrets removed from source code"),
        (True, "Input validation verified (strict Pydantic schemas)"),
        (True, "Database migrations tested (lease-locked, idempotent)"),
        (True, "Database indexes reviewed"),
        (True, "Backup procedure exists (checksummed)"),
        (False, "Restore procedure tested &mdash; TASK-13"),
        (True, "Rate limiting reviewed"),
        (True, "Timeout behavior reviewed (finite on all calls)"),
        (True, "Retry behavior reviewed (Retry-After + fallbacks)"),
        (True, "Idempotency reviewed (operation ledger, single-use tokens)"),
        (False, "Third-party failure behavior tested &mdash; TASK-2"),
        (True, "Logging configured (structlog JSON + request IDs)"),
        (True, "Sensitive-data logging reviewed"),
        (False, "Alerting configured &mdash; TASK-12"),
        (True, "Health/readiness checks configured"),
        (True, "HTTPS/TLS configured (Caddy ACME, HSTS, CSP)"),
        (False, "CI pipeline verified &mdash; red at HEAD"),
        (True, "Deployment process documented (immutable releases)"),
        (True, "Rollback procedure verified (automatic on readiness)"),
        (False, "No unresolved High launch blockers &mdash; open"),
    ]
    cl_rows = "".join(item(d, t) for d, t in cl)
    return f"""
    <h1 class="section pagebreak">7. Staged implementation roadmap</h1>
    <table>
      <tr><th style="width:12%">Stage</th><th style="width:22%">Focus</th>
          <th>Content</th></tr>
      {stage_rows}
    </table>

    <h1 class="section">8. Production launch checklist</h1>
    <table>
      <tr><th style="width:6%"></th><th>Item</th></tr>
      {cl_rows}
    </table>
    <div class="callout">Items marked &#9744; are the remaining conditions
    of the verdict. Once Stage 1 and Stage 7 items are complete and the
    Stage-8 battery is green, the verdict upgrades to <b>READY FOR
    PRODUCTION</b>.</div>
    """


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
BUILDERS = {1: build_vol1, 2: build_vol2, 3: build_vol3, 4: build_vol4,
            5: build_vol5, 6: build_vol6}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", type=int, default=0,
                        help="build a single volume (1-4)")
    parser.add_argument("--inline", action="store_true",
                        help="build in this process (default: one isolated "
                             "subprocess per volume, so heavy app imports "
                             "can never interfere across volumes)")
    args = parser.parse_args()
    volumes = [args.only] if args.only else sorted(BUILDERS)
    if args.inline:
        for vol in volumes:
            print(f"Building volume {vol} ...")
            BUILDERS[vol]()
    else:
        import subprocess
        for vol in volumes:
            print(f"Building volume {vol} (isolated process) ...")
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 "--only", str(vol), "--inline"], check=True)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

    print(f"  wrote {out_path.relative_to(ROOT)}")
