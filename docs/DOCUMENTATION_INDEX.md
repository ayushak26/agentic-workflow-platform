# Eurskem AI — Engineering Documentation Portfolio

Generated documentation for the Eurskem AI agentic workflow platform.
The PDF portfolio is produced by `scripts/build_documentation_pdf.py`,
which introspects the **live codebase** (registry, FastAPI routes, AST)
so every table entry is derived from the code itself.

Regenerate at any time:

```bash
uv run python scripts/build_documentation_pdf.py
```

## PDF Portfolio (`docs/pdf/`)

| Volume | File | Contents |
|---|---|---|
| 1 | `01_Eurskem_AI_Architecture_Overview.pdf` | System architecture, the three product surfaces, the run lifecycle, evidence & cost integrity, deployment model — with architecture diagram and product screenshots |
| 2 | `02_Eurskem_AI_Backend_Code_Reference.pdf` | Module-by-module reference for the entire `app/` package: every file, class, and public function with signature and docstring summary |
| 3 | `03_Eurskem_AI_Node_Type_Catalog.pdf` | Every registered node type: category, execution kind, config schema fields (required/optional), inputs, outputs, and author-facing guidance |
| 4 | `04_Eurskem_AI_API_Reference.pdf` | Every HTTP endpoint with method, path, authorization level, and summary |
| 5 | `05_Eurskem_AI_Node_Type_Standard.pdf` | Node type engineering standard: the six consumers of a node type, the contract, template-resolution error codes, known-issues register, remediation roadmap, and the add-a-node-type checklist |
| 6 | `06_Eurskem_AI_Production_Readiness_Assessment.pdf` | Production readiness verdict, measured baseline, feasibility scorecard, severity-ordered findings register, staged roadmap, and launch checklist |

## In-Source Documentation

Every Python module, class, and function in `app/` and `scripts/` carries a
docstring. Docstrings for the full surface were completed with the
insertion-only generator `scripts/generate_docstrings.py` (safe to re-run;
it only fills gaps and never rewrites existing documentation).

## Companion Documents

| Document | Scope |
|---|---|
| `CODEBASE_HANDBOOK.md` | Deep engineering handbook: architecture, lifecycles, security, failure modes, change-impact guide |
| `NODE_TYPES_AND_WORKFLOW_AUTHORING_GUIDE.md` | Workflow authoring and node-type usage guide |
| `VISUAL_WORKFLOW_BUILDER.md` | Builder feature documentation |
| `BUSINESS_VIEW.md` | Guided Run / business projection documentation |
| `AI_NATIVE_CHAT_NODE_EVALUATION.md` | Code-grounded evaluation of existing nodes for AI-native Chat, including the recommended node portfolio, unified source architecture, interaction model, gaps, roadmap, and validation prototype |
| `KNOWLEDGE_STUDIO_ENGINEERING_REPORT.md` | Knowledge Studio engineering report |
| `DYNAMICS_365_MCP.md` / `BUSINESS_RECORDS_MYSQL.md` | Integration documentation |
| `PREFLIGHT_AT_SCALE_DESIGN.md` | Design: generic capability-based preflight for 200–1,000+ node types |
| `OPERATIONS_RUNBOOK.md` | Production operations: rate-limit fail-closed policy, backup/restore drills, alerts, failure reference |
| `cloud-migration-map.md` | Migration planning notes |
