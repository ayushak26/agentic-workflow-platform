/**
 * Static, reusable copy for the Builder's "ⓘ Info" popovers (Problem 1).
 *
 * One entry per meaningful Builder feature — never scattered inline strings
 * in the components that use them. Rendering this never calls an LLM; the
 * "Ask AI" button next to it (see AskAiDialog.tsx) is the dynamic follow-up
 * for a question this static copy doesn't answer.
 *
 * `description` is reused verbatim as the compact context an Ask AI question
 * about this feature is grounded in (see InfoPopover.tsx) — one place this
 * copy is authored, not two.
 */

export type FeatureHelpEntry = {
  id: string;
  title: string;
  /** What is this? / What does it do? */
  description: string;
  /** When should I use it? */
  whenToUse: string;
  /** What changes in the workflow? */
  effect: string;
  example: string;
};

export const FEATURE_HELP: Record<string, FeatureHelpEntry> = {
  inputs: {
    id: 'inputs',
    title: 'Inputs',
    description: 'Declares what enters the workflow — the named, typed fields every step can address before anything runs.',
    whenToUse: 'Set this up first for a new workflow, or whenever a step needs a value that has to come from outside (an API call, an upload, a form).',
    effect: 'Adds or edits entries under the workflow\'s inputs: block. Existing steps that reference an input by name see it as soon as it is declared.',
    example: 'A triage workflow declares a text input "customer_message" — every downstream step reads it as {{inputs.customer_message}}.',
  },
  auto_layout: {
    id: 'auto_layout',
    title: 'Auto-layout',
    description: 'Automatically arranges every step on the canvas in the current flow direction, based on how they\'re connected.',
    whenToUse: 'After adding or rewiring several steps, when manual dragging has left the canvas hard to read.',
    effect: 'Only moves node positions on the canvas — it never changes a step\'s configuration, connections, or behavior. Manual positions stay put until you run this again.',
    example: 'You drop five new steps in a pile in the corner; Auto-layout spreads them left-to-right (or top-to-bottom) in connection order.',
  },
  versions: {
    id: 'versions',
    title: 'Versions',
    description: 'The saved history of this workflow — every prior Save, with a diff and one-click restore.',
    whenToUse: 'Before a risky change, or to recover a working configuration after an edit that didn\'t pan out.',
    effect: 'Restoring a version replaces the current canvas with that version\'s YAML. It does not touch anything already run in Cockpit.',
    example: 'You simplify a Decision step\'s rules, save, realize the old rules covered a case you forgot, and restore yesterday\'s version.',
  },
  preflight: {
    id: 'preflight',
    title: 'Preflight',
    description: 'A zero-token, deterministic check of the whole workflow: valid node types, valid configuration, valid connections, valid template references, a reachable entry and exit.',
    whenToUse: 'Any time before Save or Run — it catches authoring mistakes without spending a single model call or touching a real service.',
    effect: 'Reads the current canvas and reports issues; it never modifies the workflow itself. Auto-fix (next to it, when issues exist) can repair the mechanical ones.',
    example: 'A step references {{extract.summary}} but the upstream step never declared a "summary" output — Preflight flags it before you Save or Run.',
  },
  save: {
    id: 'save',
    title: 'Save',
    description: 'Writes the current canvas to the workflow\'s YAML file, after running Preflight.',
    whenToUse: 'Whenever you want this version to become the one everyone else (and Cockpit) sees — Save is what makes a change durable.',
    effect: 'Persists the workflow and adds a new entry to Versions. A workflow with unresolved Preflight errors cannot be saved.',
    example: 'You finish wiring a new branch, Save, and it now appears when the workflow is opened from the Library.',
  },
  run_test: {
    id: 'run_test',
    title: 'Run / Test',
    description: 'Executes the workflow (or a single step, via Node Testing) for real, in Cockpit, so you can see actual output instead of reasoning about the YAML.',
    whenToUse: 'After Preflight passes, to confirm the workflow actually does what you intend with real or sample inputs.',
    effect: 'Opens Cockpit with this workflow loaded and (for "Run") ready to execute end-to-end. This can call real models and, for external-action steps, take real actions.',
    example: 'You Run the workflow with a sample customer message and watch it flow through triage, drafting, and the human-approval gate.',
  },
  data_mapping: {
    id: 'data_mapping',
    title: 'Data Mapping',
    description: 'Shows exactly what a selected step can read — every upstream step\'s declared output fields — and lets you wire a field into this step\'s configuration.',
    whenToUse: 'Whenever a step\'s field should come from an earlier step\'s result rather than a fixed value.',
    effect: 'Writes a {{node_id.field}} template reference into the step\'s config. Preflight validates that the reference actually exists.',
    example: 'Mapping an AI Task\'s "input" field to {{extract.parsed.summary}} from an earlier extraction step.',
  },
  variable_picker: {
    id: 'variable_picker',
    title: 'Variable Picker',
    description: 'The field-level tool for inserting a {{node_id.field}} reference into a text field, without memorizing the exact path.',
    whenToUse: 'Anywhere you\'re writing a template string (a prompt, an email body, a rendered document) and need a value from another step.',
    effect: 'Inserts the reference at the cursor. It doesn\'t change what the referenced step produces — only how this step reads it.',
    example: 'Inside an email body, picking "customer.name" from the Input step inserts {{inputs.customer.name}}.',
  },
  node_testing: {
    id: 'node_testing',
    title: 'Node Testing',
    description: 'Runs one selected step in isolation, with sample or real upstream values, so you can see its output without executing the whole workflow.',
    whenToUse: 'While configuring a single step, especially an AI Task whose prompt/schema you\'re iterating on.',
    effect: 'Executes just that step for real (it can call a model or an external service) and shows its raw output — it never modifies the workflow.',
    example: 'Testing a classification AI Task with three sample messages to check its intent labels before wiring it into the rest of the workflow.',
  },
  branch_testing: {
    id: 'branch_testing',
    title: 'Branch Testing',
    description: 'Exercises a Router or Decision step\'s branches with sample values, to confirm each one goes where you expect.',
    whenToUse: 'After authoring or editing routing rules — especially the fallback/default branch, which is easy to leave untested.',
    effect: 'Runs the step\'s own logic against the sample values you give it and reports which branch fired and why. It doesn\'t run anything downstream.',
    example: 'Testing a Router in field mode with intent="complaint" and confirming it lands on the "Customer Service" branch, not the fallback.',
  },
  conditional_routing: {
    id: 'conditional_routing',
    title: 'Conditional Routing',
    description: 'Branching the workflow on a value: Router picks one outgoing path (by field value, business conditions, or model judgment); Decision writes named conclusions IF/THEN rules can gate on.',
    whenToUse: 'Whenever different requests should go through different steps — escalation, department routing, or any "if X then Y" business logic.',
    effect: 'Adds branch edges on the canvas, each labeled with the route name. Every deterministic route should have a fallback, or Preflight will flag the gap.',
    example: 'A Router in field mode sends "complaint" and "warranty_claim" both to a "Customer Service" branch, and anything else to a "General Inbox" fallback.',
  },
  workflow_generation: {
    id: 'workflow_generation',
    title: 'Generate a workflow from a prompt',
    description: 'Describe what you want in plain language; the platform identifies the node types it needs, drafts a workflow, checks it deterministically, and runs it once end-to-end before handing it back.',
    whenToUse: 'Starting a new workflow from scratch, or when you\'d rather describe the goal than assemble every step by hand.',
    effect: 'Produces a complete YAML workflow, opened in the Builder for you to review, adjust, and Save — nothing is saved automatically.',
    example: '"Search recent news for a competitor\'s pricing changes and draft a short tactical memo" produces a Web Search step feeding an AI Task that drafts the memo.',
  },
  undo_redo: {
    id: 'undo_redo',
    title: 'Undo / Redo',
    description: 'Steps backward or forward through your recent edits on the canvas — adding/removing steps, rewiring connections, moving nodes, changing configuration.',
    whenToUse: 'Immediately after a change you didn\'t mean to make, or to compare two recent states of the workflow.',
    effect: 'Restores an earlier (or later) in-memory snapshot of the canvas. It does not touch what\'s already Saved until you Save again.',
    example: 'You delete a step by mistake while cleaning up connections — Undo brings it straight back.',
  },
  canvas_basics: {
    id: 'canvas_basics',
    title: 'Canvas basics',
    description: 'Three ideas underpin every canvas: a connection (an edge) says data can flow from one step\'s output into another\'s input; the entry is where a run starts; the exit is what the run returns as its final result.',
    whenToUse: 'Read this when a workflow won\'t validate because of a missing connection, an unreachable step, or an undeclared exit.',
    effect: 'Dragging from one step\'s handle to another\'s draws a connection (an edge in the YAML). Entry and exit are set explicitly and checked by Preflight.',
    example: 'A step with no incoming connection and no exit declared is unreachable — Preflight reports it as an orphaned step.',
  },
};
