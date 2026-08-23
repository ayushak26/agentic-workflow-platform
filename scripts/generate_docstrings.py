#!/usr/bin/env python3
"""Generate missing docstrings across the Python codebase.

Insertion-only tool: every module, class, and function that lacks a
docstring receives one derived from its real signature (parameter names,
type annotations, return type) and the repository's recurring domain
vocabulary. Existing docstrings are never modified, and no code line is
ever rewritten - insertions happen on their own lines directly above each
block's body, which keeps the resulting diff trivially reviewable and
behaviourally inert.

Safety model:
  1. Candidate files are parsed with ``ast`` before and after editing; a
     file that fails to re-parse is left untouched.
  2. Insertions are applied bottom-up so earlier line numbers stay valid.
  3. ``--check`` reports without writing; ``--write`` applies.

Usage:
    python scripts/generate_docstrings.py --check
    python scripts/generate_docstrings.py --write
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = ("app", "scripts")

# Recurring parameter names of this codebase mapped to accurate one-line
# descriptions so generated Args sections read like they were written by
# someone who knows the system.
ARG_DESCRIPTIONS: dict[str, str] = {
    "db": "Mongo database handle",
    "request": "incoming FastAPI request",
    "response": "outgoing FastAPI response",
    "user": "authenticated current user",
    "services": "shared application services dict",
    "run_id": "workflow run identifier",
    "node_id": "workflow node identifier",
    "session_id": "session scope the record belongs to",
    "session": "session scope the record belongs to",
    "scope": "session scope the record belongs to",
    "spec": "parsed workflow specification",
    "config": "node configuration mapping",
    "resolved_config": "configuration after template resolution",
    "state": "current workflow state",
    "yaml_text": "workflow YAML text",
    "workflow_yaml": "workflow YAML text",
    "workflow_name": "workflow name",
    "name": "workflow or resource name",
    "path": "filesystem path",
    "exc": "exception that was raised",
    "error": "error value or message",
    "key": "lookup key",
    "value": "value to process",
    "token": "token value",
    "model": "model name",
    "prompt": "prompt text",
    "message": "message text",
    "inputs": "workflow input mapping",
    "output": "node output mapping",
    "result": "result mapping",
    "limit": "maximum number of items to return",
    "offset": "number of items to skip",
    "now": "current timestamp",
    "ts": "timestamp",
    "url": "target URL",
    "headers": "HTTP headers",
    "body": "request body",
    "timeout": "timeout in seconds",
    "timeout_seconds": "timeout in seconds",
    "collection_id": "knowledge collection identifier",
    "status": "status value",
    "attempt": "attempt number",
    "entries": "entries to process",
    "items": "items to process",
    "version": "version identifier",
    "version_id": "version identifier",
    "payload": "event or audit payload",
    "event": "run event",
    "evt": "run event",
    "queue": "asyncio queue",
    "redis": "Redis client",
    "client": "client instance",
    "store": "store instance",
    "ledger": "operation ledger",
    "checkpoint": "checkpoint document",
    "decision": "human decision mapping",
    "actor": "acting username",
    "reason": "reason text",
    "question": "question text",
    "content": "content value",
    "document": "document",
    "doc": "document",
    "manifest": "manifest record",
    "metadata": "metadata mapping",
    "owner": "lease owner identifier",
    "ttl_seconds": "lease TTL in seconds",
    "fields": "field names",
    "schema": "schema definition",
    "klass": "node type class",
    "node_class": "node type class",
    "type_name": "node type name",
    "manifest_entry": "manifest entry",
    "issue": "preflight issue",
    "issues": "preflight issues",
    "report": "preflight report",
    "logger": "logger instance",
    "settings": "application settings",
    "gateway": "LLM gateway",
    "entry": "ledger entry",
    "cost_usd": "cost in USD",
    "input_tokens": "input token count",
    "output_tokens": "output token count",
    "provider": "provider name",
    "intended": "intended model name",
    "resolved": "resolved model name",
    "task_type": "task type label",
    "stage": "pipeline stage label",
    "graph": "compiled LangGraph graph",
    "task": "asyncio task",
    "filename": "file name",
    "bucket": "object-store bucket name",
    "minio_key": "MinIO object key",
    "chunk_ids": "Weaviate chunk identifiers",
    "byte_size": "size in bytes",
    "original_filename": "original file name",
    "source_format": "source format",
    "password": "password value",
    "username": "username value",
    "role": "user role",
    "permission": "permission name",
    "environment": "environment name",
    "base_url": "base URL",
    "api_key": "API key",
    "raw": "raw value",
    "data": "data mapping",
    "record": "record",
    "records": "records",
    "docs": "documents",
    "query": "query filter",
    "projection": "Mongo projection",
    "sort": "Mongo sort specification",
    "cursor": "cursor",
    "collection": "Mongo collection",
    "pipeline": "aggregation pipeline",
    "updates": "update mapping",
    "default": "default value",
    "required": "required flag",
    "expected": "expected value",
    "actual": "actual value",
    "source": "source value",
    "target": "target value",
    "parent": "parent value",
    "child": "child value",
    "prefix": "prefix string",
    "suffix": "suffix string",
    "pattern": "regex pattern",
    "match": "regex match",
    "parts": "path segments",
    "row": "table row",
    "rows": "table rows",
    "column": "column name",
    "table": "table name",
    "page": "page number",
    "index": "index",
    "start": "start value",
    "end": "end value",
    "first": "first value",
    "last": "last value",
    "current": "current value",
    "previous": "previous value",
    "old": "previous value",
    "new": "new value",
    "src": "source value",
    "dst": "destination value",
    "active": "active flag",
    "flag": "flag value",
    "dry_run": "dry-run flag",
    "force": "force flag",
    "strict": "strict flag",
    "recursive": "recursive flag",
    "verbose": "verbose flag",
    "multiple": "multiple flag",
    "optional": "optional flag",
    "kwargs": "keyword arguments",
    "args": "positional arguments",
}



# Leading snake-case tokens mapped to imperative verbs for summary lines.
VERB_MAP: dict[str, str] = {
    "get": "Return", "list": "List", "find": "Find", "load": "Load",
    "read": "Read", "fetch": "Fetch", "save": "Save", "write": "Write",
    "create": "Create", "build": "Build", "make": "Build",
    "ensure": "Ensure", "validate": "Validate", "check": "Check",
    "verify": "Verify", "parse": "Parse", "format": "Format",
    "render": "Render", "resolve": "Resolve", "compute": "Compute",
    "calculate": "Compute", "count": "Count", "delete": "Delete",
    "remove": "Remove", "drop": "Drop", "clear": "Clear",
    "update": "Update", "set": "Set", "mark": "Mark", "put": "Store",
    "add": "Add", "append": "Append", "register": "Register",
    "push": "Push", "handle": "Handle", "process": "Process",
    "run": "Run", "execute": "Execute", "apply": "Apply",
    "record": "Record", "emit": "Emit", "publish": "Publish",
    "send": "Send", "download": "Download", "upload": "Upload",
    "initialize": "Initialize", "init": "Initialize",
    "configure": "Configure", "close": "Close", "shutdown": "Shut down",
    "release": "Release", "acquire": "Acquire", "renew": "Renew",
    "cancel": "Cancel", "resume": "Resume", "pause": "Pause",
    "start": "Start", "stop": "Stop", "launch": "Launch",
    "dispatch": "Dispatch", "deliver": "Deliver", "reserve": "Reserve",
    "claim": "Claim", "mint": "Mint", "derive": "Derive",
    "extract": "Extract", "project": "Project", "normalize": "Normalize",
    "sanitize": "Sanitize", "serialize": "Serialize",
    "deserialize": "Deserialize", "encode": "Encode", "decode": "Decode",
    "hash": "Hash", "sign": "Sign", "wrap": "Wrap", "unwrap": "Unwrap",
    "merge": "Merge", "split": "Split", "join": "Join",
    "filter": "Filter", "sort": "Sort", "group": "Group",
    "map": "Map", "convert": "Convert", "compare": "Compare",
    "select": "Select", "choose": "Choose", "pick": "Pick",
    "rank": "Rank", "score": "Score", "classify": "Classify",
    "detect": "Detect", "discover": "Discover", "probe": "Probe",
    "ping": "Ping", "sync": "Synchronize", "reconcile": "Reconcile",
    "backfill": "Backfill", "migrate": "Migrate", "seed": "Seed",
    "ingest": "Ingest", "chunk": "Chunk", "embed": "Embed",
    "retrieve": "Retrieve", "search": "Search", "query": "Query",
    "summarize": "Summarize", "translate": "Translate",
    "generate": "Generate", "draft": "Draft", "compose": "Compose",
    "assemble": "Assemble", "materialize": "Materialize",
    "hydrate": "Hydrate", "finalize": "Finalize",
    "complete": "Complete", "abort": "Abort", "retry": "Retry",
    "sweep": "Sweep", "prune": "Prune", "expire": "Expire",
    "reset": "Reset", "restore": "Restore", "rollback": "Roll back",
    "mount": "Mount", "serve": "Serve", "stream": "Stream",
    "subscribe": "Subscribe", "unsubscribe": "Unsubscribe",
    "notify": "Notify", "broadcast": "Broadcast",
    "authorize": "Authorize", "authenticate": "Authenticate",
    "redact": "Redact", "pseudonymize": "Pseudonymize",
    "tokenize": "Tokenize", "detokenize": "Detokenize",
    "encrypt": "Encrypt", "decrypt": "Decrypt", "rotate": "Rotate",
    "refresh": "Refresh", "copy": "Copy", "move": "Move",
    "rename": "Rename", "print": "Print", "log": "Log",
    "raise": "Raise", "report": "Report", "upsert": "Upsert",
    "is": "Return whether",
    "has": "Return whether", "can": "Return whether",
    "should": "Return whether", "needs": "Return whether",
}

# Package docstring summaries keyed by dotted package path.
PACKAGE_DESCRIPTIONS: dict[str, str] = {
    "app": "Eurskem AI application package",
    "app.api": "HTTP API layer: FastAPI routers for auth, workflows, runs, knowledge, and administration",
    "app.db": "Database access layer: Mongo connectivity and versioned migrations",
    "app.evidence": "Evidence lifecycle: sourcing, weighting, and verification of evidence records",
    "app.evaluation": "Evaluation harness: golden sets, judges, and scorecards",
    "app.ingestion": "Document ingestion: extraction, chunking, and collection management",
    "app.integrations": "External integrations: email, file providers, and outbound actions",
    "app.integrations.email": "Email integration adapters and OAuth-connected mailboxes",
    "app.integrations.files": "Cloud file integration adapters and token vault",
    "app.knowledge": "Knowledge Studio control plane: resources, profiles, and indexes",
    "app.llm": "LLM gateway layer: provider gateways, routing, fallbacks, and cost recording",
    "app.mcp": "MCP client wiring and in-tree MCP servers",
    "app.mcp.business_records": "Business-records MCP server over the MySQL database",
    "app.mcp.d365_finance": "Dynamics 365 Finance and Operations MCP server (fixture-backed mock)",
    "app.mcp.dynamics": "Dynamics 365 CRM MCP server (mock/live modes)",
    "app.nodes": "Workflow node type implementations: every registered node agent",
    "app.observability": "Observability: structured logging, Prometheus metrics, tracing, and the cost ledger",
    "app.proposal_graph": "Proposal workspace graph: domain state and workspace store",
    "app.rag": "RAG pipeline composition",
    "app.research": "Research capabilities: skills loading and bounded deep research",
    "app.retrieval": "Retrieval service: reranking, compression, and traces",
    "app.runtime": "Workflow runtime: schema, loader, compiler, preflight, executor, HITL, and events",
    "app.security": "Security layer: auth, RBAC, middleware, guardrails, and entity protection",
    "app.storage": "Object storage abstraction over MinIO/S3",
    "app.tools": "Node-facing tools: web search, vision, image generation, and rendering",
    "app.workflow": "Workflow services: run history, builder store, chat workflows, and prompt templates",
    "scripts": "Operational and development scripts",
}



# --------------------------------------------------------------------------
# Name and signature helpers
# --------------------------------------------------------------------------

def _words(name: str) -> list[str]:
    return [part for part in name.strip('_').split('_') if part]


def _phrase(name: str) -> str:
    return ' '.join(_words(name)) or name


def _annotation_text(annotation: ast.expr | None) -> str:
    if annotation is None:
        return ''
    try:
        return ast.unparse(annotation)
    except Exception:
        return ''


def _default_text(default: ast.expr | None) -> str:
    if default is None:
        return ''
    try:
        text = ast.unparse(default)
    except Exception:
        return ''
    return text if len(text) <= 40 else '...'


def _describe_arg(name: str) -> str:
    if name in ARG_DESCRIPTIONS:
        return ARG_DESCRIPTIONS[name]
    return f'the {_phrase(name)}'


def _summary_for_function(node, is_property: bool, is_init: bool,
                          class_name: str | None) -> str:
    name = node.name
    tokens = _words(name)
    if is_property:
        return f'The {_phrase(name)}.'
    if is_init:
        owner = class_name or 'the object'
        return f'Initialize the {owner}.'
    if name.startswith('__') and name.endswith('__'):
        return f'Implement the ``{name}`` protocol.'
    if not tokens:
        return f'Perform the {name} operation.'
    head = tokens[0]
    verb = VERB_MAP.get(head)
    rest = ' '.join(tokens[1:])
    if verb == 'Return whether':
        subject = rest or 'the condition holds'
        return f'Return whether {subject}.'
    if verb:
        obj = rest or 'result'
        return f'{verb} the {obj}.'
    if name.startswith('_'):
        return f'Internal helper for the {_phrase(name)} step.'
    return f'Compute the {_phrase(name)}.'


def _returns_section(node, is_property: bool) -> list[str]:
    if is_property:
        return []
    annotation = node.returns
    if annotation is None:
        return []
    text = _annotation_text(annotation)
    if text in ('None', 'NoneType'):
        return []
    tokens = _words(node.name)
    if tokens and tokens[0] in ('is', 'has', 'can', 'should', 'needs'):
        noun = ' '.join(tokens[1:]) or 'the condition holds'
        return ['Returns:', f'    bool: True when {noun}.']
    noun = ' '.join(tokens[1:]) if len(tokens) > 1 else 'result'
    return ['Returns:', f'    {text}: The {noun}.']


def _args_section(node) -> list[str]:
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    skip = {'self', 'cls'}
    entries: list[tuple[str, str, str | None]] = []
    defaults = list(args.defaults)
    pad = len(positional) - len(defaults)
    for i, arg in enumerate(positional):
        if arg.arg in skip:
            continue
        ann = _annotation_text(arg.annotation)
        default = None
        if i - pad >= 0:
            default = _default_text(defaults[i - pad])
        entries.append((arg.arg, ann, default))
    if args.vararg:
        entries.append((f'*{args.vararg.arg}',
                        _annotation_text(args.vararg.annotation), None))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        entries.append((arg.arg, _annotation_text(arg.annotation),
                        _default_text(default)))
    if args.kwarg:
        entries.append((f'**{args.kwarg.arg}',
                        _annotation_text(args.kwarg.annotation), None))
    if not entries:
        return []
    lines = ['Args:']
    for name, ann, default in entries[:16]:
        desc = _describe_arg(name.lstrip('*'))
        if default:
            desc += f' (optional, default {default})'
        desc = desc[0].upper() + desc[1:]
        if ann:
            lines.append(f'    {name} ({ann}): {desc}.')
        else:
            lines.append(f'    {name}: {desc}.')
    if len(entries) > 16:
        lines.append('    ...: remaining parameters follow the same pattern.')
    return lines


def _class_summary(node: ast.ClassDef) -> str:
    base_names: list[str] = []
    for base in node.bases:
        try:
            base_names.append(ast.unparse(base))
        except Exception:
            continue
    name_phrase = _phrase(node.name)
    if any(b == 'NodeType' for b in base_names):
        return f'Workflow node type implementing the {name_phrase} capability.'
    if any('BaseModel' in b for b in base_names):
        return f'Pydantic model defining the {name_phrase} shape.'
    if any(b in ('Enum', 'IntEnum') or b.endswith('Enum') for b in base_names):
        return f'Enumeration of {name_phrase} values.'
    if any('Exception' in b or 'Error' in b for b in base_names):
        return f'Exception raised for the {name_phrase} case.'
    if any(b in ('ABC', 'ABCMeta') for b in base_names):
        return f'Abstract base defining the {name_phrase} contract.'
    return f'Provides the {name_phrase} behaviour.'


def _class_doc(node: ast.ClassDef) -> list[str]:
    lines = [_class_summary(node), '']
    attrs: list[str] = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann = _annotation_text(stmt.annotation)
            attrs.append(f'    {stmt.target.id} ({ann}).' if ann
                         else f'    {stmt.target.id}.')
        if len(attrs) >= 8:
            break
    if attrs:
        lines.append('Attributes:')
        lines.extend(attrs)
    while lines and lines[-1] == '':
        lines.pop()
    return lines


def _function_doc(node, is_property: bool, class_name: str | None) -> list[str]:
    is_init = node.name == '__init__'
    lines = [_summary_for_function(node, is_property, is_init, class_name), '']
    args = _args_section(node)
    if args:
        lines.extend(args)
        lines.append('')
    ret = _returns_section(node, is_property)
    if ret:
        lines.extend(ret)
        lines.append('')
    while lines and lines[-1] == '':
        lines.pop()
    return lines


def _module_doc(path: Path, tree: ast.Module) -> list[str]:
    rel = path.relative_to(ROOT)
    parts = list(rel.with_suffix('').parts)
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    dotted = '.'.join(parts)
    summary = PACKAGE_DESCRIPTIONS.get(dotted)
    if summary:
        lines = [summary + '.', '']
    else:
        title = _phrase(path.stem)
        cap = title[0].upper() + title[1:] if title else path.stem
        lines = [f'{cap} module.', '']
        pkg = '.'.join(parts[:-1])
        pkg_desc = PACKAGE_DESCRIPTIONS.get(pkg)
        if pkg_desc:
            lines.append(f'Part of the {pkg_desc.lower()}.')
    symbols: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not stmt.name.startswith('_'):
                symbols.append(stmt.name)
    if symbols:
        shown = ', '.join(symbols[:6])
        more = f', ... ({len(symbols)} symbols total)' if len(symbols) > 6 else ''
        lines.append('')
        lines.append(f'Public symbols: {shown}{more}.')
    while lines and lines[-1] == '':
        lines.pop()
    return lines


def _render_docstring(lines: list[str], indent: str) -> list[str]:
    safe = [ln.replace('"""', "'''") for ln in lines]
    if len(safe) == 1:
        return [f'{indent}"""{safe[0]}"""']
    out = [f'{indent}"""{safe[0]}']
    for ln in safe[1:]:
        out.append(f'{indent}{ln}' if ln else '')
    out.append(f'{indent}"""')
    return out


def _decorator_names(node) -> set[str]:
    names = set()
    for dec in getattr(node, 'decorator_list', []):
        try:
            names.add(ast.unparse(dec).split('(')[0])
        except Exception:
            pass
    return names


def _enclosing_class(tree: ast.Module, target) -> str | None:
    for klass in ast.walk(tree):
        if isinstance(klass, ast.ClassDef) and target in klass.body:
            return klass.name
    return None


def collect_insertions(tree: ast.Module, path: Path):
    insertions: list[tuple[int, str, list[str]]] = []
    if ast.get_docstring(tree) is None and tree.body:
        first = tree.body[0]
        insertions.append((first.lineno - 1, '', _module_doc(path, tree)))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.body and ast.get_docstring(node) is None:
                first = node.body[0]
                if first.lineno <= node.lineno:
                    continue  # single-line class; no clean insertion point
                indent = ' ' * first.col_offset
                insertions.append(
                    (first.lineno - 1, indent, _class_doc(node)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.body or ast.get_docstring(node) is not None:
                continue
            first = node.body[0]
            sig_end = node.lineno
            returns = node.returns
            if returns is not None:
                sig_end = returns.end_lineno or node.lineno
            else:
                sig_end = getattr(node.args, 'end_lineno', None) or node.lineno
            if first.lineno <= sig_end:
                continue  # body shares the signature line; no insertion point
            decs = _decorator_names(node)
            is_property = 'property' in decs or 'cached_property' in decs
            owner = _enclosing_class(tree, node)
            indent = ' ' * first.col_offset
            insertions.append((first.lineno - 1, indent,
                               _function_doc(node, is_property, owner)))
    return insertions


def process_file(path: Path, write: bool) -> int:
    source = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    insertions = collect_insertions(tree, path)
    if not insertions:
        return 0
    lines = source.splitlines()
    for lineno, indent, doc_lines in sorted(insertions,
                                            key=lambda t: t[0],
                                            reverse=True):
        lines[lineno:lineno] = _render_docstring(doc_lines, indent)
    new_source = '\n'.join(lines) + ('\n' if source.endswith('\n') else '')
    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        print(f'  SKIP (post-edit parse failed): {path}: {exc}')
        return 0
    if write:
        path.write_text(new_source, encoding='utf-8')
    return len(insertions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true',
                        help='apply insertions (default: report only)')
    parser.add_argument('--only', default='',
                        help='restrict to paths containing this substring')
    args = parser.parse_args()

    total_files = 0
    total_insertions = 0
    for dirname in TARGET_DIRS:
        base = ROOT / dirname
        for path in sorted(base.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            if args.only and args.only not in str(path):
                continue
            count = process_file(path, write=args.write)
            if count:
                total_files += 1
                total_insertions += count
                verb = 'WROTE' if args.write else 'WOULD ADD'
                print(f'{verb} {count:4d} docstrings  '
                      f'{path.relative_to(ROOT)}')
    mode = 'Applied' if args.write else 'Would add'
    print(f'\n{mode} {total_insertions} docstrings across '
          f'{total_files} files.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

