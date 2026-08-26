#!/usr/bin/env python3
"""Generate the canonical 24-volume codebase documentation portfolio.

The generator inspects the current repository (Python AST, TypeScript source,
FastAPI declarations, workflow YAML, tests, deployment files, and existing
engineering guides), renders A4 PDFs with WeasyPrint, and atomically replaces
``docs/pdf``. The output directory is validated to contain exactly 24 PDFs.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import yaml
from markdown_it import MarkdownIt
from pypdf import PdfReader
from weasyprint import CSS, HTML

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "pdf"
TODAY = date.today().isoformat()
MD = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})

VOLUMES: tuple[tuple[str, str, str], ...] = (
    ("00_CODEBASE_START_HERE.pdf", "Codebase Start Here", "Orientation, reading path, setup, and the system mental model."),
    ("01_SYSTEM_ARCHITECTURE.pdf", "System Architecture", "Boundaries, services, topology, and principal request flows."),
    ("02_TECH_STACK_AND_DECISIONS.pdf", "Tech Stack and Decisions", "Technology inventory and evidence-backed design choices."),
    ("03_BACKEND_CODE_REFERENCE.pdf", "Backend Code Reference", "Package, module, class, and function reference for app/."),
    ("04_FRONTEND_CODE_REFERENCE.pdf", "Frontend Code Reference", "React modes, components, hooks, API clients, and types."),
    ("05_FRONTEND_BACKEND_MAPPING.pdf", "Frontend–Backend Mapping", "UI surfaces mapped to API routes, stores, and runtime behavior."),
    ("06_WORKFLOW_ENGINE_AND_NODE_TYPES.pdf", "Workflow Engine and Node Types", "Workflow contracts, compiler, preflight, registry, and node catalog."),
    ("07_RUNTIME_EXECUTION_AND_STATE.pdf", "Runtime Execution and State", "Execution lifecycle, state transitions, retries, HITL, and resume."),
    ("08_DATA_STORAGE_AND_STATE_MANAGEMENT.pdf", "Data Storage and State Management", "MongoDB, Redis, MinIO, Weaviate, checkpoints, and browser state."),
    ("09_AI_LLM_MODEL_ROUTING.pdf", "AI, LLM, and Model Routing", "Providers, model routing, fallback, policy, and cost accounting."),
    ("10_RAG_KNOWLEDGE_AND_RETRIEVAL.pdf", "RAG, Knowledge, and Retrieval", "Ingestion, indexing, secure filtering, hybrid search, and grounded generation."),
    ("11_MCP_TOOLS_AND_INTEGRATIONS.pdf", "MCP, Tools, and Integrations", "MCP discovery, business systems, email, Drive, and external actions."),
    ("12_API_REFERENCE_AND_REQUEST_FLOWS.pdf", "API Reference and Request Flows", "HTTP route catalog, authorization, payloads, and sequences."),
    ("13_SECURITY_AUTH_AND_DATA_PROTECTION.pdf", "Security, Auth, and Data Protection", "Identity, RBAC, ownership, isolation, middleware, and sensitive data."),
    ("14_EVENTS_ASYNC_AND_DISTRIBUTED_COORDINATION.pdf", "Events, Async, and Distributed Coordination", "SSE, Redis streams, background work, leases, and multi-worker behavior."),
    ("15_TESTING_DEBUGGING_AND_OBSERVABILITY.pdf", "Testing, Debugging, and Observability", "Test layers, diagnostics, logging, metrics, traces, and QA evidence."),
    ("16_DEPLOYMENT_CONFIGURATION_AND_OPERATIONS.pdf", "Deployment, Configuration, and Operations", "Configuration, containers, CI/CD, deployment, backup, and recovery."),
    ("17_FILE_BY_FILE_CODE_REFERENCE.pdf", "File-by-File Code Reference", "Purpose and major symbols for every first-party source file."),
    ("18_CODE_LOGIC_REFERENCE.pdf", "Code Logic Reference", "Important functions, methods, branching logic, invariants, and failure behavior."),
    ("19_DOCSTRINGS_AND_CODE_DOCUMENTATION.pdf", "Docstrings and Code Documentation", "Documentation coverage, public symbols, conventions, and gaps."),
    ("20_ARCHITECTURE_TRADEOFFS_TECH_DEBT.pdf", "Architecture Tradeoffs and Tech Debt", "Confirmed compromises, coupling, risks, removals, and remediation guidance."),
    ("21_CHANGE_IMPACT_GUIDE.pdf", "Change Impact Guide", "Change scenarios, affected layers, required tests, and rollout checks."),
    ("22_FEATURE_TO_CODE_MAP.pdf", "Feature-to-Code Map", "Product capabilities mapped to UI, API, runtime, storage, and tests."),
    ("23_MASTER_INDEX.pdf", "Master Index", "Portfolio-wide topic, package, endpoint, node, feature, and file index."),
)
EXPECTED_NAMES = {item[0] for item in VOLUMES}

CSS_TEXT = r"""
@page { size:A4; margin:18mm 15mm 20mm;
 @bottom-center { content:counter(page) " / " counter(pages); color:#7b8494; font-size:8pt }
 @bottom-right { content:"Eurskem AI Engineering Reference"; color:#7b8494; font-size:7pt }}
@page cover { margin:0; @bottom-center { content:none } @bottom-right { content:none }}
* { box-sizing:border-box } body { font-family:"Helvetica Neue",Arial,sans-serif; font-size:9.2pt; line-height:1.46; color:#202735 }
.cover { page:cover; width:210mm; height:297mm; padding:38mm 24mm; color:white; background:linear-gradient(145deg,#081325,#12294b 56%,#075b65) }
.cover .series { text-transform:uppercase; letter-spacing:.24em; color:#78dfca; font-size:9pt }
.cover h1 { font-size:35pt; line-height:1.08; margin:12mm 0 6mm; max-width:160mm }
.cover .sub { font-size:14pt; color:#d3deec; max-width:150mm }.cover .meta { margin-top:42mm; color:#a8bad0; line-height:2 }
h1 { color:#0b5260; font-size:20pt; border-bottom:2px solid #18a28b; padding-bottom:2mm; margin:9mm 0 4mm }
h2 { color:#142e50; font-size:14pt; margin:7mm 0 2mm } h3 { color:#0b5260; font-size:11.5pt; margin:5mm 0 1.5mm }
p { margin:0 0 3mm } ul,ol { margin:1mm 0 4mm 6mm; padding-left:4mm }
code,.path { font-family:Menlo,Consolas,monospace; font-size:7.7pt; color:#075b65; overflow-wrap:anywhere }
code { background:#eef3f6; padding:.4mm 1mm; border-radius:2px }
pre { font-family:Menlo,Consolas,monospace; font-size:7.5pt; line-height:1.4; white-space:pre-wrap; background:#101a2b; color:#e3eaf3; padding:4mm; border-radius:4px }
table { width:100%; border-collapse:collapse; margin:2.5mm 0 5mm; font-size:7.6pt; table-layout:fixed }
th { background:#142e50; color:white; text-align:left; padding:1.8mm 2mm; overflow-wrap:anywhere }
td { border-bottom:.3pt solid #d9e0e8; padding:1.5mm 2mm; vertical-align:top; overflow-wrap:anywhere } tr:nth-child(even) td { background:#f6f8fa }
.callout { border-left:4px solid #18a28b; background:#eef8f6; padding:3mm 4mm; margin:4mm 0 }
.metric { display:inline-block; min-width:30mm; margin:1mm; padding:3mm; background:#edf3f8; border-radius:4px }.metric b { display:block; color:#075b65; font-size:16pt }
.pagebreak { break-before:page }.small { font-size:7.5pt }.muted { color:#667085 }
"""

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md", ".yaml", ".yml", ".toml", ".json", ".sh", ".sql"}
SOURCE_ROOTS = ("app", "ui/src", "ui/e2e", "scripts", "deploy", "observability", ".github/workflows")
EXCLUDED = {"__pycache__", "node_modules", ".venv", ".git", ".claude", "dist", "playwright-report", "playwright-artifacts"}


@dataclass
class Symbol:
    path: str
    line: int
    kind: str
    name: str
    signature: str
    summary: str
    documented: bool
    complexity: int = 1


@dataclass
class Route:
    method: str
    path: str
    handler: str
    source: str
    line: int
    summary: str
    auth: str


@dataclass
class Node:
    name: str
    category: str
    source: str
    line: int
    services: str
    description: str


@dataclass
class Model:
    files: list[Path] = field(default_factory=list)
    py_symbols: list[Symbol] = field(default_factory=list)
    ts_symbols: list[Symbol] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    workflows: list[dict[str, Any]] = field(default_factory=list)
    tests: list[Path] = field(default_factory=list)
    summaries: dict[str, str] = field(default_factory=dict)
    imports: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    sha: str = "unknown"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def sentence(value: str, fallback: str = "No summary declared.") -> str:
    clean = " ".join((value or "").strip().split())
    if not clean:
        return fallback
    match = re.search(r"(?<=[.!?])\s", clean)
    return clean[:match.start() + 1] if match else clean[:300]


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], widths: Sequence[str] = ()) -> str:
    cols = "".join(f'<col style="width:{esc(width)}">' for width in widths)
    head = "".join(f"<th>{esc(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def markdown(value: str) -> str:
    return MD.render(value)


def source_files() -> list[Path]:
    result: set[Path] = set()
    for root_name in SOURCE_ROOTS:
        root = ROOT / root_name
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not any(part in EXCLUDED for part in path.parts):
                    result.add(path)
    for name in ("README.md", "pyproject.toml", "Dockerfile", "docker-compose.yml", "docker-compose.production.yml", ".env.example"):
        path = ROOT / name
        if path.exists():
            result.add(path)
    return sorted(result, key=rel)


def expr_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{expr_name(node.value)}.{node.attr}".strip(".")
    return ""


def literal(node: ast.AST | None, default: str = "") -> str:
    try:
        value = ast.literal_eval(node) if node is not None else default
        return str(value) if isinstance(value, (str, int, float)) else default
    except Exception:
        return default


def args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return ast.unparse(node.args)
    except Exception:
        return "…"


def complexity(node: ast.AST) -> int:
    kinds = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.BoolOp, ast.IfExp, ast.Match, ast.comprehension)
    return 1 + sum(isinstance(item, kinds) for item in ast.walk(node))


def class_value(node: ast.ClassDef, name: str) -> ast.AST | None:
    for statement in node.body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            return statement.value
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id == name:
            return statement.value
    return None


def inspect_python(path: Path, model: Model) -> None:
    text, relative = read(path), rel(path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        model.summaries[relative] = "Python source not parsed by the documentation pass."
        return
    model.summaries[relative] = sentence(ast.get_docstring(tree) or "", "Python module.")
    prefix = ""
    for item in tree.body:
        if isinstance(item, (ast.Import, ast.ImportFrom)):
            for alias in item.names:
                model.imports[relative].add(item.module if isinstance(item, ast.ImportFrom) and item.module else alias.name)
        if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "router" for target in item.targets) and isinstance(item.value, ast.Call) and expr_name(item.value.func).endswith("APIRouter"):
            prefix = next((literal(kw.value) for kw in item.value.keywords if kw.arg == "prefix"), "")
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(item) or ""
            model.py_symbols.append(Symbol(relative, item.lineno, "async function" if isinstance(item, ast.AsyncFunctionDef) else "function", item.name, args(item), sentence(doc), bool(doc), complexity(item)))
        elif isinstance(item, ast.ClassDef):
            doc = ast.get_docstring(item) or ""
            model.py_symbols.append(Symbol(relative, item.lineno, "class", item.name, ", ".join(expr_name(base) for base in item.bases), sentence(doc), bool(doc), complexity(item)))
            for member in item.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and not member.name.startswith("__"):
                    mdoc = ast.get_docstring(member) or ""
                    model.py_symbols.append(Symbol(relative, member.lineno, "method", f"{item.name}.{member.name}", args(member), sentence(mdoc), bool(mdoc), complexity(member)))
            if any(expr_name(base).endswith("NodeType") for base in item.bases) and any("NodeRegistry.register" in expr_name(deco.func if isinstance(deco, ast.Call) else deco) for deco in item.decorator_list):
                services = "none declared"
                for member in item.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == "required_services":
                        returns = [found.value for found in ast.walk(member) if isinstance(found, ast.Return) and found.value]
                        if returns:
                            try:
                                services = ", ".join(ast.literal_eval(returns[0]))
                            except Exception:
                                services = "dynamic"
                model.nodes.append(Node(literal(class_value(item, "type_name"), item.name), literal(class_value(item, "category"), "Uncategorized"), relative, item.lineno, services, sentence(literal(class_value(item, "description"), doc))))
    for item in tree.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in item.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            match = re.search(r"(?:router|app)\.(get|post|put|patch|delete|head|options)$", expr_name(deco.func))
            if not match:
                continue
            segment = ast.get_source_segment(text, item) or ""
            auth = "admin" if "require_admin" in segment else "consultant" if "require_consultant" in segment else "authenticated" if "CurrentUser" in segment or "get_current_user" in segment else "public"
            model.routes.append(Route(match.group(1).upper(), prefix + literal(deco.args[0] if deco.args else None, "") or "/", item.name, relative, item.lineno, sentence(ast.get_docstring(item) or ""), auth))


TS_DECL = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(function|class|interface|type|const|enum)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)


def inspect_frontend(path: Path, model: Model) -> None:
    text, relative = read(path), rel(path)
    comment = re.search(r"/\*\*([\s\S]*?)\*/|^\s*//\s*(.+)$", text, re.MULTILINE)
    model.summaries[relative] = sentence(re.sub(r"^\s*\*\s?", "", (comment.group(1) or comment.group(2)), flags=re.MULTILINE) if comment else "", "React/TypeScript module.")
    for match in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", text):
        model.imports[relative].add(match.group(1))
    lines = text.splitlines()
    for match in TS_DECL.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        context = "\n".join(lines[max(0, line - 4):line])
        documented = "/**" in context or "//" in context
        model.ts_symbols.append(Symbol(relative, line, match.group(1), match.group(2), "", "Documented frontend declaration." if documented else "Frontend declaration.", documented))


def build_model() -> Model:
    model = Model(sha=git_sha(), files=source_files())
    for path in model.files:
        if path.suffix == ".py":
            inspect_python(path, model)
        elif rel(path).startswith("ui/") and path.suffix in {".ts", ".tsx", ".js", ".jsx", ".css"}:
            inspect_frontend(path, model)
        else:
            model.summaries[rel(path)] = sentence(read(path), f"{path.suffix.lstrip('.').upper() or 'Project'} file.")
    workflow_root = ROOT / "workflows"
    for path in sorted([*workflow_root.rglob("*.yaml"), *workflow_root.rglob("*.yml")], key=rel):
        if any(part in EXCLUDED for part in path.parts):
            continue
        try:
            data = yaml.safe_load(read(path)) or {}
        except Exception:
            data = {}
        nodes = data.get("nodes", []) if isinstance(data, dict) else []
        model.workflows.append({"path": rel(path), "name": data.get("name") or data.get("title") or path.stem, "nodes": len(nodes), "edges": len(data.get("edges", [])) if isinstance(data, dict) else 0, "types": [str(node.get("type", "unknown")) for node in nodes if isinstance(node, dict)]})
    model.tests = [path for path in model.files if path.name.startswith("test_") or ".test." in path.name or ".spec." in path.name]
    model.routes.sort(key=lambda item: (item.path, item.method))
    model.nodes.sort(key=lambda item: item.name.lower())
    return model


def metrics(model: Model) -> str:
    values = (("First-party files", len(model.files)), ("Python symbols", len(model.py_symbols)), ("Frontend symbols", len(model.ts_symbols)), ("HTTP routes", len(model.routes)), ("Node types", len(model.nodes)), ("Workflow YAML", len(model.workflows)), ("Test files", len(model.tests)))
    return "<div>" + "".join(f'<span class="metric"><b>{count}</b>{esc(label)}</span>' for label, count in values) + "</div>"


def key_files(model: Model, paths: Sequence[str]) -> str:
    return table(("File", "Responsibility"), ((f'<span class="path">{esc(path)}</span>', esc(model.summaries.get(path, "Relevant implementation file."))) for path in paths if (ROOT / path).exists()), ("38%", "62%"))


def matching(model: Model, prefixes: Sequence[str], terms: Sequence[str] = ()) -> list[str]:
    return [rel(path) for path in model.files if any(rel(path).startswith(prefix) for prefix in prefixes) and (not terms or any(term.lower() in (rel(path) + " " + model.summaries.get(rel(path), "")).lower() for term in terms))]


def package_table(model: Model, frontend: bool = False) -> str:
    prefix = "ui/src/" if frontend else "app/"
    groups: dict[str, list[str]] = defaultdict(list)
    for path in model.files:
        rp = rel(path)
        if not rp.startswith(prefix):
            continue
        parts = rp.split("/")
        group = "/".join(parts[:3] if frontend and len(parts) > 3 else parts[:2] if not frontend and len(parts) > 2 else parts[:-1])
        groups[group].append(rp)
    symbols = model.ts_symbols if frontend else model.py_symbols
    rows = []
    for group, files in sorted(groups.items()):
        rows.append((f'<span class="path">{esc(group)}</span>', str(len(files)), str(sum(symbol.path in files for symbol in symbols)), esc(model.summaries.get(files[0], "Source area."))))
    return table(("Package / area", "Files", "Symbols", "Representative responsibility"), rows, ("28%", "10%", "10%", "52%"))


def symbol_table(symbols: Sequence[Symbol]) -> str:
    return table(("Symbol", "Kind", "Location", "Signature / summary"), ((f'<b>{esc(item.name)}</b>', esc(item.kind), f'<span class="path">{esc(item.path)}:{item.line}</span>', f'<code>{esc(item.signature)}</code><br>{esc(item.summary)}') for item in symbols), ("22%", "10%", "28%", "40%"))


def route_table(routes: Sequence[Route]) -> str:
    return table(("Method", "Path", "Auth", "Handler", "Purpose"), ((f'<b>{esc(item.method)}</b>', f'<span class="path">{esc(item.path)}</span>', esc(item.auth), f'<span class="path">{esc(item.source)}:{item.line}</span><br>{esc(item.handler)}', esc(item.summary)) for item in routes), ("8%", "25%", "11%", "25%", "31%"))


def node_table(nodes: Sequence[Node]) -> str:
    return table(("Node type", "Category", "Source", "Services", "Purpose"), ((f'<b>{esc(item.name)}</b>', esc(item.category), f'<span class="path">{esc(item.source)}:{item.line}</span>', esc(item.services), esc(item.description)) for item in nodes), ("18%", "13%", "22%", "15%", "32%"))


def workflow_table(model: Model, limit: int = 140) -> str:
    items = sorted(model.workflows, key=lambda item: (-item["nodes"], item["path"]))[:limit]
    return table(("Workflow", "Nodes", "Edges", "Node types"), ((f'<span class="path">{esc(item["path"])}</span><br>{esc(item["name"])}', str(item["nodes"]), str(item["edges"]), esc(", ".join(sorted(set(item["types"])))[:700])) for item in items), ("33%", "8%", "8%", "51%"))


def v00(m: Model) -> str:
    return markdown("""# What this system is

Eurskem AI is a multi-user agentic workflow platform. Users author typed workflow graphs, validate them before execution, run them through a durable asynchronous runtime, observe live progress, pause for human decisions, and produce traceable research or business artifacts. The React application exposes Workflow Studio, Knowledge, evaluation, cost, chat, and operations surfaces over a FastAPI backend.

## First-day reading path

1. Read this volume and the system architecture.
2. Follow one browser request through the frontend–backend map and API reference.
3. Learn workflow contracts and the runtime lifecycle.
4. Use volumes 17–23 as lookup references while changing code.

## Repository map

- `app/api`: authenticated HTTP boundaries.
- `app/runtime`: workflow schema, validation, compilation, execution, events, and HITL.
- `app/nodes`: typed capabilities registered into the node manifest.
- `app/workflow`: durable workflow, conversation, run, file, and history stores.
- `app/llm`, `app/retrieval`, `app/rag`, `app/knowledge`: AI and grounded retrieval.
- `ui/src`: React SPA, API client, modes, components, and client state.
- `workflows`: executable and reference YAML graphs.
""") + metrics(m) + markdown("""## Core invariants

- Workflows pass schema and preflight checks before provider tokens are consumed.
- Node outputs are validated against declared contracts.
- Security scope is resolved server-side and cannot be widened by caller metadata.
- Durable run records live outside process memory.
- External writes use authorization, policy, and idempotency controls.
""") + key_files(m, ["README.md", "app/main.py", "app/config.py", "app/runtime/schema.py", "app/runtime/compiler.py", "app/runtime/preflight.py", "app/nodes/base.py", "app/api/runs.py", "ui/src/App.tsx", "ui/src/api/client.ts"])


def v01(m: Model) -> str:
    return markdown("""# Architectural model

The browser communicates through HTTP and authenticated event streams. FastAPI routes validate identity and payloads, invoke runtime or store functions, and persist state through MongoDB, MinIO, Weaviate, and Redis-backed coordination. The compiler turns YAML into a LangGraph execution graph whose nodes resolve from `NodeRegistry`.

```text
React SPA → FastAPI middleware/routes → workflow/runtime services → typed nodes
                                          ↓                 ↓
                          MongoDB · Redis · MinIO · Weaviate · LLM/MCP providers
                                          ↓
                            SSE events, artifacts, histories, costs
```

## Boundary rules

- UI code calls backend APIs and never imports backend modules.
- API routes own authentication and HTTP error translation.
- Runtime owns graph semantics and delegates capability work to nodes.
- Nodes receive infrastructure through shared services.
- Persistent stores define durable user-visible state.
""") + package_table(m) + key_files(m, ["app/main.py", "app/config.py", "app/db/mongo.py", "app/runtime/events.py", "app/runtime/coordination.py", "docker-compose.yml", "docker-compose.production.yml"])


def dependencies() -> list[tuple[str, str, str]]:
    py = re.findall(r'^\s*"([^"@<>=\s]+)(?:[^\"]*)"', read(ROOT / "pyproject.toml"), re.MULTILINE)
    package = json.loads(read(ROOT / "ui/package.json") or "{}")
    frontend = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    reasons = {"fastapi":"Typed asynchronous HTTP API.","pydantic":"Executable schemas and configuration.","redis":"Coordination, events, limits, checkpoints.","minio":"Protected object storage.","weaviate-client":"Vector and hybrid retrieval.","langgraph":"Compiled graph execution.","mcp":"Discoverable integration tools.","weasyprint":"Paged HTML/CSS PDF rendering.","react":"Component SPA.","reactflow":"Visual workflow graph.","typescript":"Frontend contracts.","vite":"Frontend build tooling.","vitest":"Frontend tests.","@playwright/test":"Browser tests."}
    return [(name, "Python", reasons.get(name, "Declared backend dependency.")) for name in py] + [(name, "Frontend", reasons.get(name, "Declared frontend dependency.")) for name in frontend]


def v02(m: Model) -> str:
    return markdown("""# Decision posture

The stack favors typed contracts, explicit infrastructure, and deployable open components. FastAPI and Pydantic keep API and node contracts near executable code. React and TypeScript make the Builder schema-driven. MongoDB stores evolving run documents, Redis coordinates ephemeral distributed behavior, MinIO protects artifacts, and Weaviate serves retrieval.

## Major decisions

- Typed graph nodes over unconstrained agent loops.
- Server-resolved security scope rather than prompt-enforced isolation.
- SSE with replay for primarily server-to-client progress.
- Provider registry and fallback with cost-audited substitutions.
- HTML/CSS for PDF and native libraries for Office formats.
""") + table(("Dependency", "Layer", "Purpose"), ((f'<code>{esc(a)}</code>', esc(b), esc(c)) for a,b,c in dependencies()), ("30%", "15%", "55%"))


def v03(m: Model) -> str:
    return markdown("# Backend package inventory") + package_table(m) + markdown("# Backend symbols") + symbol_table([item for item in m.py_symbols if item.path.startswith("app/")])


def v04(m: Model) -> str:
    return markdown("""# Frontend architecture

The frontend is a React SPA. State is local or context-based rather than centralized in Redux/Zustand. API clients own authentication-aware requests and streaming; modes own server-state loading and presentation; the Builder bridges YAML contracts to React Flow.
""") + package_table(m, True) + markdown("# TypeScript and TSX declarations") + symbol_table(m.ts_symbols)


def v05(m: Model) -> str:
    api_names = {item.name for item in m.ts_symbols if item.path.startswith("ui/src/api/")}
    rows = []
    for path in m.files:
        rp = rel(path)
        if not rp.endswith((".ts", ".tsx")) or rp.startswith("ui/src/api/"):
            continue
        calls = sorted(set(re.findall(r"\b(?:api\.)?([A-Za-z_$][\w$]*)\s*\(", read(path))) & api_names)
        if calls:
            rows.append((f'<span class="path">{esc(rp)}</span>', esc(", ".join(calls)), esc(m.summaries.get(rp, "Frontend surface."))))
    return markdown("# Generated UI-to-client mapping") + table(("Frontend file", "API client calls", "Responsibility"), rows, ("38%", "28%", "34%")) + markdown("# Server routes") + route_table(m.routes)


def v06(m: Model) -> str:
    return markdown("""# Workflow contract

A workflow is versioned YAML with inputs, variables, typed nodes, and directed edges. Pydantic validates shape. Preflight checks topology, templates, services, models, and node-specific requirements. Compilation resolves each node type and builds executable LangGraph behavior.

1. Builder or YAML defines the graph.
2. Schema validation rejects malformed contracts.
3. Preflight reports actionable issues.
4. Compiler binds node runners and conditional routes.
5. Runtime validates outputs and persists progress.
""") + key_files(m, ["app/runtime/schema.py", "app/runtime/preflight.py", "app/runtime/logic_preflight.py", "app/runtime/templating.py", "app/runtime/compiler.py", "app/nodes/base.py", "app/nodes/__init__.py", "ui/src/modes/studio/Builder.tsx"]) + markdown("# Registered node catalog") + node_table(m.nodes) + markdown("# Largest workflow graphs") + workflow_table(m)


def v07(m: Model) -> str:
    return markdown("""# Execution lifecycle

Run creation establishes owner scope and a durable record. The compiler constructs graph execution. Every node receives resolved configuration and shared services, returns schema-validated output, emits progress, and records status. Failures are classified and persisted. Human gates create resumable state; resume tokens and checkpoints constrain replay.

## State classes

- Workflow definition: versioned authoring contract.
- Run document: durable status, inputs, outputs, errors, and timing.
- Graph checkpoint: resumable execution position.
- Event stream: bounded live/replay progress, not canonical history.
- Conversation state: user-facing turns layered over workflow runs.
""") + key_files(m, matching(m, ("app/runtime/", "app/workflow/"), ("run", "hitl", "checkpoint", "state", "orchestration", "resume"))[:100])


def v08(m: Model) -> str:
    stores = (("MongoDB","Users, workflows, conversations, runs, histories, policies, costs","Durable document source of truth"),("Redis","Rate limits, leases, SSE, pub/sub, checkpoints","Distributed coordination and replay"),("MinIO","Uploads, workflow files, generated artifacts","Protected binary/object storage"),("Weaviate","Chunks, embeddings, metadata, hybrid retrieval","Search index constrained by owner scope"),("Browser memory","Current mode, loaded data, forms","Ephemeral presentation state"),("local/session storage","Preferences and attachment handoff","Non-authoritative client convenience"))
    return markdown("# Storage responsibility matrix") + table(("Store", "Data", "Role"), ((esc(a),esc(b),esc(c)) for a,b,c in stores), ("18%", "47%", "35%")) + key_files(m, matching(m, ("app/db/", "app/storage/", "app/workflow/", "app/retrieval/", "ui/src/"), ("store", "mongo", "redis", "minio", "weaviate", "storage", "state"))[:140])


def v09(m: Model) -> str:
    return markdown("""# Model access architecture

Nodes request models through the shared LLM gateway. The registry resolves aliases and provider metadata; policy selects eligible models; contextual clones bind run, session, and node IDs for cost attribution. Retry and fallback record intended and actual models.

- Model identifiers are registry validated.
- Provider calls use finite timeouts and bounded retries.
- Usage is recorded at run/node boundaries.
- Policy-sensitive decisions fail closed.
- Structured output is parsed and validated.
""") + key_files(m, matching(m, ("app/llm/", "app/observability/", "app/security/", "app/nodes/"), ("llm", "model", "router", "provider", "cost", "policy", "ai_task", "transform"))[:120])


def v10(m: Model) -> str:
    files = matching(m, ("app/ingestion/", "app/retrieval/", "app/rag/", "app/knowledge/", "app/api/knowledge", "ui/src/modes/knowledge"))
    return markdown("""# Grounded knowledge flow

Documents enter protected object storage, are extracted and chunked, embedded, and indexed with ownership and provenance. Retrieval compiles mandatory security filters separately from model-generated metadata. Hybrid search combines lexical and vector signals; reranking/compression produce cited chunks for RAG generation.

`session_id`/owner and collection scope are mandatory. Selected `document_ids` are server-resolved provenance scope, not generic caller-controlled metadata. Cache identity includes document scope.
""") + key_files(m, files[:160]) + symbol_table([item for item in m.py_symbols if item.path.startswith(("app/ingestion/", "app/retrieval/", "app/rag/", "app/knowledge/"))])


def v11(m: Model) -> str:
    files = matching(m, ("app/mcp/", "app/integrations/", "app/tools/", "app/api/", "ui/src/"), ("mcp", "email", "drive", "integration", "external_action", "database", "dynamics"))
    return markdown("""# Integration model

MCP servers expose discoverable tools and schemas. The Builder reads live manifests instead of hardcoding every capability. Integration nodes execute through configured services; writes are subject to identity, policy, idempotency, and audit controls. Adapters cover email, Dynamics, business records, Drive, web search, databases, and external HTTP actions.
""") + key_files(m, files[:180]) + route_table([item for item in m.routes if any(term in item.path for term in ("mcp", "integration", "drive", "oauth", "email", "builder"))])


def v12(m: Model) -> str:
    counts = Counter(item.auth for item in m.routes)
    return markdown("# API surface") + "".join(f'<span class="metric"><b>{count}</b>{esc(name)} routes</span>' for name,count in sorted(counts.items())) + route_table(m.routes) + markdown("""# Principal flows

## Workflow run
Browser submits inputs → route resolves scope → durable run is created → background owner executes → nodes and events persist → browser streams progress and fetches final artifacts.

## Chat turn
Browser sends message/sources → planner resolves managed workflow and authorized scope → run-chat executes → answer extraction retries malformed empty structures → conversation persists.

## Knowledge query
Browser selects collection/documents → API verifies ownership → retrieval compiles mandatory filters → search/rerank returns provenance → RAG generates cited output.
""")


def v13(m: Model) -> str:
    return markdown("""# Security model

Authentication establishes a user; route dependencies enforce roles. Resource handlers verify ownership. Middleware adds request context, size limits, trusted hosts, CORS, and Redis rate limits. Retrieval scope is enforced in query compilation, not prompts. Protected files are proxied through authenticated routes.

- Secrets come from configuration and are not returned in manifests.
- Entity tokenization can replace sensitive values before model calls.
- External actions are constrained and audited.
- Production rate limiting fails closed when Redis is unavailable.
- Security filters are separate from self-query metadata filters.
""") + key_files(m, matching(m, ("app/security/", "app/api/auth", "app/retrieval/", "app/storage/", "ui/src/components/auth"))[:160]) + route_table(m.routes)


def v14(m: Model) -> str:
    return markdown("""# Coordination model

Long-running workflows execute beyond request latency. A background manager and Redis leases coordinate ownership. `RunEventBus` writes bounded replay records and publishes live updates; clients reconnect with an event cursor. MongoDB remains durable history.

- Lost ownership expires through leases.
- Duplicate starts/resumes are constrained by operation and token semantics.
- SSE reconnects replay recent events then reconcile with durable runs.
- Redis loss affects coordination but does not redefine persisted history.
""") + key_files(m, matching(m, ("app/runtime/", "app/workflow/", "app/api/", "ui/src/api/"), ("event", "async", "background", "coordination", "lease", "stream", "checkpoint", "orchestration"))[:120])


def v15(m: Model) -> str:
    kinds = Counter("Playwright" if "e2e/" in rel(path) or ".spec." in path.name else "Vitest" if "ui/src" in rel(path) else "Pytest" for path in m.tests)
    return markdown("# Test portfolio") + "".join(f'<span class="metric"><b>{count}</b>{esc(name)} files</span>' for name,count in sorted(kinds.items())) + table(("Test file", "Layer"), ((f'<span class="path">{esc(rel(path))}</span>', esc("Browser E2E" if ".spec." in path.name else "Frontend unit/component" if "ui/src" in rel(path) else "Backend unit/integration")) for path in m.tests), ("70%", "30%")) + markdown("""# Debugging and observability

Structured logs carry request/run context. Prometheus metrics expose API, workflow, model fallback, and rate-limit behavior. Run History and Cockpit expose node failures. Retrieval traces explain query/filter/search stages. Cost records preserve intended versus actual model use.

Debug from narrowest layer outward: schema/preflight → node unit → API/store integration → UI component → Playwright → live provider smoke.
""") + key_files(m, matching(m, ("app/observability/", "observability/", "scripts/", "ui/e2e/"), ("log", "metric", "smoke", "test", "alert", "trace"))[:120])


def v16(m: Model) -> str:
    return markdown("""# Operational model

Development and production containerize FastAPI with MongoDB, Redis, MinIO, and Weaviate. Production uses immutable releases, Caddy TLS/edge controls, readiness gates, smoke checks, and rollback. Backup scripts capture state with checksums.

- `/health` is liveness; `/ready` probes dependencies.
- Restore Redis rather than disabling fail-closed rate limiting.
- Copy backups off-host and perform restore drills.
- Migration failure prevents serving traffic.
- Alert rules need an on-call routing destination.
""") + key_files(m, [".env.example", "app/config.py", "Dockerfile", "docker-compose.yml", "docker-compose.production.yml", "docs/OPERATIONS_RUNBOOK.md"] + matching(m, ("deploy/", ".github/workflows/", "observability/", "scripts/"))[:120]) + symbol_table([item for item in m.py_symbols if item.path == "app/config.py"])


def v17(m: Model) -> str:
    by_file: dict[str, list[str]] = defaultdict(list)
    for item in m.py_symbols + m.ts_symbols:
        by_file[item.path].append(item.name)
    return markdown("# Complete first-party file inventory") + table(("File", "Purpose", "Major symbols"), ((f'<span class="path">{esc(rel(path))}</span>', esc(m.summaries.get(rel(path), "Project file.")), esc(", ".join(by_file.get(rel(path), [])[:12]) or "—")) for path in m.files), ("34%", "42%", "24%"))


def v18(m: Model) -> str:
    items = sorted([item for item in m.py_symbols if item.kind in {"function", "async function", "method"}], key=lambda item: (-item.complexity, item.path, item.line))
    return markdown("""# Reading logic safely

The branch score is a lightweight AST count, not a quality verdict. High scores identify orchestration, validation, parsing, and fallback code that deserves narrow changes and strong regression tests. Read each unit with callers, state mutations, external effects, and error translation.
""") + table(("Logic unit", "Location", "Branch score", "Contract"), ((f'<b>{esc(item.name)}</b>', f'<span class="path">{esc(item.path)}:{item.line}</span>', str(item.complexity), f'<code>{esc(item.signature)}</code><br>{esc(item.summary)}') for item in items), ("24%", "25%", "10%", "41%"))


def v19(m: Model) -> str:
    all_symbols = m.py_symbols + m.ts_symbols
    return markdown("# Documentation coverage") + f'<span class="metric"><b>{sum(item.documented for item in m.py_symbols)}/{len(m.py_symbols)}</b>Python documented</span><span class="metric"><b>{sum(item.documented for item in m.ts_symbols)}/{len(m.ts_symbols)}</b>Frontend documented</span>' + markdown("""## Conventions

Public Python modules/classes/functions should explain purpose, boundaries, invariants, and non-obvious failures. TypeScript components/hooks should document state ownership and unusual effects. Comments should explain why constraints exist rather than restating syntax.
""") + symbol_table([item for item in all_symbols if not item.documented])


def v20(m: Model) -> str:
    return markdown("""# Confirmed tradeoffs

| Decision | Benefit | Cost / debt |
|---|---|---|
| Shared services mapping for nodes | Easy construction and test fakes | String-key contracts and runtime missing-service checks |
| MongoDB document stores | Flexible evolving run/workflow records | Cross-document invariants require explicit indexes and code |
| Redis for coordination concerns | One dependency for leases, streams, limits, checkpoints | Redis outage has broad operational impact |
| Local React state/effects | Low framework overhead | Repeated loading/error patterns and possible stale state |
| Schema-driven node registry | Builder/runtime alignment | Registry imports and manifest generation are critical paths |
| Generated Chat adapters | Reuses governed workflow runtime | Cache identity must include every semantic scope |

# Current debt and risk

- Some older prose predates removal of Pipeline and Business View subsystems; generated inventories are authoritative.
- Alert routing is less complete than alert rule generation.
- Application orchestration spans routes, runtime helpers, and stores rather than one service layer.
- Large/high-branch modules should only be decomposed behind regression coverage.
- Generated QA artifacts need an explicit retention policy.
""") + symbol_table(sorted(m.py_symbols, key=lambda item: -item.complexity)[:140])


SCENARIOS = (("Add/change node type","nodes, manifest, preflight, Builder, workflow fixtures","Conformance, preflight, workflow execution, UI build"),("Change workflow schema","schema/compiler/templates, Builder YAML bridge, versions","Schema, preflight, round-trip, migration compatibility"),("Add API endpoint","route, main wiring, UI client/types, auth","Authorization, API integration, component regression"),("Change execution","compiler/HITL/events, stores, Cockpit/history","Retry/resume, replay, durable state, E2E"),("Change retrieval filters","filter compiler/search, RAG, Knowledge UI","Isolation, document scope, cache identity"),("Add model/provider","registry/router/client, config, pricing, preflight","Routing, fallback, cost, live smoke"),("Add integration/MCP tool","registry/client, service, node, Builder discovery","Schema, auth/policy/idempotency, fake server"),("Change storage schema","store, migration, indexes, backup/restore, API","Migration idempotency and restore compatibility"),("Change security","dependencies/middleware/tokens, ownership, UI rehydrate","Negative auth, expiry/replay, rate-limit behavior"),("Change frontend state","App/modes/hooks/storage","Vitest, refresh restore, responsive Playwright"),("Change deployment/config","config/env/compose/Caddy/scripts/CI","Startup, readiness, smoke, rollback, backup"))


def v21(m: Model) -> str:
    return markdown("""# Impact discipline

Start from the executable contract, enumerate consumers, preserve compatibility where persisted state exists, and test narrow layers before broad release gates. Security, migrations, caches, and generated artifacts are common hidden impacts.
""") + table(("Change", "Likely impact", "Minimum validation"), ((esc(a),esc(b),esc(c)) for a,b,c in SCENARIOS), ("24%", "41%", "35%")) + package_table(m)


FEATURES = (("Business Chat and managed sources","ui/src/modes/studio/business-chat","app/api/chat_workspace.py; app/api/run_chat.py","chat workflow/conversation/run stores","chat workspace E2E"),("Visual workflow Builder","ui/src/modes/studio/Builder.tsx","app/api/builder.py; workflows.py","workflow files and versions","Builder and route tests"),("Workflow execution/Cockpit","ui/src/modes/studio/Cockpit.tsx","app/api/runs.py","runtime/events/run history","run, HITL, E2E"),("Knowledge Studio","ui/src/modes/knowledge","app/api/knowledge.py","MinIO, Weaviate, collections","knowledge/retrieval tests"),("RAG agents","retrieval selectors","app/api/rag_agents.py","RAG/retrieval/nodes","RAG service tests"),("LLM providers","model selectors","app/api/llm_providers.py","registry/router/cost","routing and smoke tests"),("MCP/integrations","Builder tool panels","app/api/builder.py","MCP and integrations","MCP/provider tests"),("Files/artifacts","source/output UI","workflow file routes","MinIO and renderers","file/renderer tests"),("Evaluation Lab","ui/src/modes/eval","app/api/eval.py","evaluation package","evaluation tests"),("Cost management","ui/src/modes/cost","cost APIs","cost ledger","pricing/cost tests"),("Authentication","ui/src/components/auth","app/api/auth.py","security modules","auth/RBAC tests"),("Operations","health/run history UI","health/metrics routes","deploy/compose/alerts","deployment smoke"))


def v22(m: Model) -> str:
    counts = Counter(node for workflow in m.workflows for node in workflow["types"])
    return markdown("# Product capability map") + table(("Feature", "Frontend", "API", "Core/storage", "Tests"), ((esc(a),f'<span class="path">{esc(b)}</span>',f'<span class="path">{esc(c)}</span>',f'<span class="path">{esc(d)}</span>',esc(e)) for a,b,c,d,e in FEATURES), ("18%", "20%", "20%", "24%", "18%")) + markdown("# Workflow capability usage") + table(("Node type", "Workflow occurrences"), ((f'<b>{esc(name)}</b>', str(count)) for name,count in counts.most_common()), ("65%", "35%"))


def v23(m: Model) -> str:
    volumes = [(f"{i:02d}", f'<b>{esc(title)}</b><br><span class="path">{esc(name)}</span>', esc(desc)) for i,(name,title,desc) in enumerate(VOLUMES)]
    entries = []
    for node in m.nodes:
        entries.append((node.name, "Node type", node.source, "06, 17, 22"))
    for route in m.routes:
        entries.append((f"{route.method} {route.path}", "API route", route.source, "12"))
    for feature, frontend, api, core, _ in FEATURES:
        entries.append((feature, "Feature", f"{frontend}; {api}; {core}", "22"))
    for name,count in Counter("/".join(rel(path).split("/")[:2]) for path in m.files).items():
        entries.append((name, f"Package/area ({count} files)", name, "01, 03/04, 17"))
    return markdown("# Volume directory") + table(("Vol", "Document", "Scope"), volumes, ("8%", "42%", "50%")) + markdown("# Master topic and symbol index") + table(("Entry", "Kind", "Primary source", "Volumes"), ((esc(a),esc(b),f'<span class="path">{esc(c)}</span>',esc(d)) for a,b,c,d in sorted(entries, key=lambda item: item[0].lower())), ("27%", "18%", "40%", "15%"))


BUILDERS: tuple[Callable[[Model], str], ...] = (v00,v01,v02,v03,v04,v05,v06,v07,v08,v09,v10,v11,v12,v13,v14,v15,v16,v17,v18,v19,v20,v21,v22,v23)


def document(index: int, body: str, model: Model) -> str:
    _, title, description = VOLUMES[index]
    cover = f'<section class="cover"><div class="series">Engineering Documentation Portfolio · Volume {index:02d}</div><h1>{esc(title)}</h1><div class="sub">{esc(description)}</div><div class="meta">Eurskem AI · Agentic Workflow Platform<br>Generated {TODAY}<br>Code revision {esc(model.sha)}<br>Derived from the live repository</div></section>'
    notice = '<div class="callout"><b>Source of truth.</b> Inventories and symbol tables are generated from the current checkout. Narrative explains observed structures and does not replace executable contracts.</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title></head><body>{cover}{notice}{body}</body></html>"


def render(index: int, model: Model, directory: Path) -> Path:
    path = directory / VOLUMES[index][0]
    HTML(string=document(index, BUILDERS[index](model), model), base_url=str(ROOT)).write_pdf(
        path,
        stylesheets=[CSS(string=CSS_TEXT)],
    )
    return path


def validate(directory: Path) -> list[dict[str, Any]]:
    actual = {path.name for path in directory.glob("*.pdf")}
    if actual != EXPECTED_NAMES:
        raise RuntimeError(f"PDF set mismatch; missing={sorted(EXPECTED_NAMES-actual)}, extra={sorted(actual-EXPECTED_NAMES)}")
    results = []
    for name,title,_ in VOLUMES:
        path = directory / name
        if path.read_bytes()[:5] != b"%PDF-":
            raise RuntimeError(f"Invalid PDF signature: {name}")
        reader = PdfReader(str(path))
        if not reader.pages:
            raise RuntimeError(f"No PDF pages: {name}")
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
        normalized_text = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
        normalized_title = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
        if normalized_title not in normalized_text:
            raise RuntimeError(f"Title missing from PDF text: {name}")
        results.append({"name":name,"pages":len(reader.pages),"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    return results


def update_index(results: list[dict[str, Any]], model: Model) -> None:
    pages = {item["name"]: item["pages"] for item in results}
    lines = ["# Eurskem AI — Engineering Documentation Portfolio","",f"Generated from commit `{model.sha}` on {TODAY}.","","```bash","uv run python scripts/build_documentation_pdf.py","```","","## Canonical PDF Portfolio (`docs/pdf/`)","","| # | PDF | Scope | Pages |","|---:|---|---|---:|"]
    for index,(name,title,description) in enumerate(VOLUMES):
        lines.append(f"| {index:02d} | `{name}` | **{title}.** {description} | {pages[name]} |")
    lines.extend(["","The generator enforces that `docs/pdf/` contains exactly these 24 valid PDF files.",""])
    (ROOT / "docs" / "DOCUMENTATION_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def build_one(index: int, directory: Path) -> Path:
    """Render one volume in a fresh process-friendly operation."""
    if not 0 <= index < 24:
        raise ValueError("--only must be between 0 and 23")
    directory.mkdir(parents=True, exist_ok=True)
    return render(index, build_model(), directory)


def build(only: int | None = None, output_dir: Path | None = None) -> list[dict[str, Any]]:
    if only is not None:
        path = build_one(only, output_dir or OUT_DIR)
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        print(f"wrote {display_path} ({len(PdfReader(str(path)).pages)} pages)")
        return []
    temp = Path(tempfile.mkdtemp(prefix="documentation-pdf-", dir=ROOT / "docs"))
    try:
        for index in range(24):
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--only",
                    str(index),
                    "--output-dir",
                    str(temp),
                ],
                cwd=ROOT,
                check=True,
            )
        results = validate(temp)
        model = build_model()
        if OUT_DIR.exists():
            shutil.rmtree(OUT_DIR)
        temp.replace(OUT_DIR)
        update_index(results, model)
        return results
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", type=int, default=None, help="build one volume (0-23) without replacing the complete portfolio")
    parser.add_argument("--output-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--validate-only", action="store_true", help="validate docs/pdf without rebuilding")
    args = parser.parse_args()
    results = validate(OUT_DIR) if args.validate_only else build(args.only, args.output_dir)
    if results:
        print(json.dumps({"pdfs":len(results),"pages":sum(item["pages"] for item in results),"bytes":sum(item["bytes"] for item in results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())