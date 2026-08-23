import type { WorkflowFileReference } from '../../../api/types';

export const EMPTY_CHAT_RESULT = 'The workflow completed successfully but did not produce a user-visible result.';

export type ChatArtifactKind = 'image' | 'pdf' | 'docx' | 'pptx' | 'xlsx';

export type ChatArtifact = {
  kind: ChatArtifactKind;
  key: string;
  title: string;
  contentType?: string;
  byteSize?: number;
  pageCount?: number;
  estimatedPageCount?: number;
  slideCount?: number;
  sheetCount?: number;
  rowCount?: number;
  provider?: string;
  model?: string;
  reference?: WorkflowFileReference;
  /** Artifacts emitted by the same output object are siblings. */
  siblingGroup: string;
};

export type ChatOutput =
  | { kind: 'text'; text: string }
  | { kind: 'code'; code: string; language: string | null; filename?: string }
  | ChatArtifact;

export type ChatOutputRun = {
  outputs?: Record<string, unknown> | null;
  node_runs?: Record<string, { output?: unknown } | null> | null;
  node_types?: Record<string, string> | null;
};

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']);
const ARTIFACT_EXTENSIONS = new Set<ChatArtifactKind>(['pdf', 'docx', 'pptx', 'xlsx']);
const ARTIFACT_FIELDS = ['pdf_key', 'docx_key', 'pptx_key', 'xlsx_key', 'file_key', 'output_key', 'minio_key', 'key'];
const NON_VISIBLE_KEYS = new Set([
  'byte_size', 'content_type', 'file_id', 'kind', 'minio_key', 'parseable_text',
  'sha256', 'page_count', 'estimated_page_count', 'page_count_basis', 'slide_count',
  'sheet_count', 'row_count', 'total_rows', 'provider', 'model', 'generated',
  'pdf_key', 'docx_key', 'pptx_key', 'xlsx_key', 'file_key', 'output_key', 'key',
]);

export function isWorkflowFileReference(value: unknown): value is WorkflowFileReference {
  return Boolean(
    value
    && typeof value === 'object'
    && (value as Record<string, unknown>).kind === 'workflow_file'
    && typeof (value as Record<string, unknown>).file_id === 'string'
    && typeof (value as Record<string, unknown>).minio_key === 'string',
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function extensionOf(key: string): string {
  return key.split(/[?#]/, 1)[0].split('.').pop()?.toLowerCase() ?? '';
}

function numberField(source: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    if (typeof source[key] === 'number' && Number.isFinite(source[key])) return source[key];
  }
  return undefined;
}

function titleFromKey(key: string, fallback?: string): string {
  if (fallback?.trim()) return fallback;
  const basename = key.split('/').pop();
  return basename && basename.trim() ? basename : 'Workflow output';
}

function artifactKind(key: string, contentType = ''): ChatArtifactKind | null {
  if (contentType.startsWith('image/')) return 'image';
  const extension = extensionOf(key);
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  return ARTIFACT_EXTENSIONS.has(extension as ChatArtifactKind)
    ? extension as ChatArtifactKind
    : null;
}

function fromReference(ref: WorkflowFileReference, siblingGroup: string): ChatArtifact | null {
  const kind = artifactKind(ref.minio_key || ref.name, ref.content_type || '');
  if (!kind) return null;
  return {
    kind,
    key: ref.minio_key,
    title: ref.name,
    contentType: ref.content_type,
    byteSize: ref.size_bytes,
    reference: ref,
    siblingGroup,
  };
}

function artifactFromKey(
  key: string,
  metadata: Record<string, unknown>,
  siblingGroup: string,
): ChatArtifact | null {
  const contentType = typeof metadata.content_type === 'string' ? metadata.content_type : '';
  const kind = artifactKind(key, contentType);
  if (!kind) return null;
  const title = typeof metadata.name === 'string'
    ? metadata.name
    : typeof metadata.filename === 'string'
      ? metadata.filename
      : undefined;
  return {
    kind,
    key,
    title: titleFromKey(key, title),
    contentType: contentType || undefined,
    byteSize: numberField(metadata, 'byte_size', 'size_bytes'),
    pageCount: numberField(metadata, 'page_count'),
    estimatedPageCount: numberField(metadata, 'estimated_page_count'),
    slideCount: numberField(metadata, 'slide_count'),
    sheetCount: numberField(metadata, 'sheet_count'),
    rowCount: numberField(metadata, 'row_count', 'total_rows'),
    provider: typeof metadata.provider === 'string' ? metadata.provider : undefined,
    model: typeof metadata.model === 'string' ? metadata.model : undefined,
    siblingGroup,
  };
}

function collectArtifacts(value: unknown, siblingGroup: string, found: ChatArtifact[]): boolean {
  if (isWorkflowFileReference(value)) {
    const artifact = fromReference(value, siblingGroup);
    if (artifact) found.push(artifact);
    return Boolean(artifact);
  }
  if (Array.isArray(value)) {
    let collected = false;
    value.forEach(item => {
      collected = collectArtifacts(item, siblingGroup, found) || collected;
    });
    return collected;
  }
  const object = record(value);
  if (!object) return false;

  let collected = false;
  for (const field of ARTIFACT_FIELDS) {
    const key = object[field];
    if (typeof key !== 'string') continue;
    const artifact = artifactFromKey(key, object, siblingGroup);
    if (!artifact) continue;
    found.push(artifact);
    collected = true;
  }
  for (const [key, child] of Object.entries(object)) {
    if (ARTIFACT_FIELDS.includes(key)) continue;
    if (isWorkflowFileReference(child) || Array.isArray(child) || record(child)) {
      collected = collectArtifacts(child, `${siblingGroup}:${key}`, found) || collected;
    }
  }
  return collected;
}

function dedupeAndApplySiblingPrecedence(artifacts: ChatArtifact[]): ChatArtifact[] {
  const unique = new Map<string, ChatArtifact>();
  for (const artifact of artifacts) {
    const previous = unique.get(artifact.key);
    if (!previous || (!previous.reference && artifact.reference)) unique.set(artifact.key, artifact);
  }
  const values = [...unique.values()];
  const groupsWithPdf = new Set(values.filter(item => item.kind === 'pdf').map(item => item.siblingGroup));
  return values.filter(item => item.kind !== 'docx' || !groupsWithPdf.has(item.siblingGroup));
}

/** Split Markdown fenced code while preserving text/code ordering. */
export function splitFencedCode(text: string): ChatOutput[] {
  const outputs: ChatOutput[] = [];
  const fence = /```([^\n`]*)\n([\s\S]*?)```/g;
  let cursor = 0;
  for (const match of text.matchAll(fence)) {
    const index = match.index ?? 0;
    const before = text.slice(cursor, index).trim();
    if (before) outputs.push({ kind: 'text', text: before });
    const info = match[1].trim().split(/\s+/).filter(Boolean);
    outputs.push({
      kind: 'code',
      code: match[2].replace(/\n$/, ''),
      language: info[0] ?? null,
      ...(info[1] ? { filename: info[1] } : {}),
    });
    cursor = index + match[0].length;
  }
  const after = text.slice(cursor).trim();
  if (after) outputs.push({ kind: 'text', text: after });
  return outputs.length > 0 ? outputs : [{ kind: 'text', text }];
}

function humanize(key: string): string {
  return key.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function scalarText(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

function summarizeRows(rows: unknown[]): string | null {
  if (rows.length === 0) return null;
  const previews = rows.slice(0, 3).map((row, index) => {
    const object = record(row);
    if (!object) return `${index + 1}. ${scalarText(row) ?? 'Result'}`;
    const fields = Object.entries(object).slice(0, 4).map(([key, value]) => (
      `${humanize(key)}: ${scalarText(value) ?? '[structured value]'}`
    ));
    return `${index + 1}. ${fields.join(' · ')}`;
  });
  const remainder = rows.length > previews.length ? `\n…and ${rows.length - previews.length} more.` : '';
  return `Found ${rows.length} row${rows.length === 1 ? '' : 's'}.\n${previews.join('\n')}${remainder}`;
}

/** Convert structured data to labelled readable text, never a raw JSON dump. */
export function structuredValueAsText(value: unknown, label?: string): string | null {
  const scalar = scalarText(value);
  if (scalar !== null) return label ? `${humanize(label)}: ${scalar}` : scalar;
  if (Array.isArray(value)) {
    if (value.length === 0) return null;
    if (value.every(item => record(item))) return summarizeRows(value);
    const items = value.slice(0, 5).map(item => scalarText(item) ?? '[structured value]');
    return `${label ? `${humanize(label)}: ` : ''}${items.join(', ')}${value.length > 5 ? `, and ${value.length - 5} more` : ''}`;
  }
  const object = record(value);
  if (!object) return null;
  for (const rowsKey of ['rows', 'records', 'items', 'results']) {
    const rows = object[rowsKey];
    if (Array.isArray(rows) && rows.length > 0 && rows.every(item => record(item))) {
      const summary = summarizeRows(rows);
      if (summary) return label ? `${humanize(label)}\n${summary}` : summary;
    }
  }
  const lines = Object.entries(object)
    .filter(([key, child]) => !NON_VISIBLE_KEYS.has(key) && child !== null && child !== undefined)
    .slice(0, 12)
    .map(([key, child]) => {
      const childScalar = scalarText(child);
      if (childScalar !== null) return `${humanize(key)}: ${childScalar}`;
      if (Array.isArray(child)) return `${humanize(key)}: ${child.length} item${child.length === 1 ? '' : 's'}`;
      return `${humanize(key)}: [structured value]`;
    });
  if (lines.length === 0) return null;
  return label ? `${humanize(label)}\n${lines.join('\n')}` : lines.join('\n');
}

function appendText(outputs: ChatOutput[], text: string): void {
  for (const output of splitFencedCode(text)) outputs.push(output);
}

/** Normalize the primary visible result. Sources and inspector state stay outside this union. */
export function normalizeChatOutputs(run: ChatOutputRun): ChatOutput[] {
  const visible: ChatOutput[] = [];
  const artifacts: ChatArtifact[] = [];
  const nodeRuns = run.node_runs ?? {};
  const nodeTypes = run.node_types ?? {};
  const chatMessages = new Set<string>();

  for (const [nodeId, nodeRun] of Object.entries(nodeRuns)) {
    if (nodeTypes[nodeId] !== 'EndAgent') continue;
    const message = record(nodeRun?.output)?.chat_message;
    if (typeof message === 'string' && message.trim()) {
      chatMessages.add(message.trim());
      appendText(visible, message.trim());
    }
  }

  const structured: string[] = [];
  for (const [key, value] of Object.entries(run.outputs ?? {})) {
    const hasArtifact = collectArtifacts(value, `output:${key}`, artifacts);
    if (hasArtifact && (isWorkflowFileReference(value) || Array.isArray(value))) continue;
    const text = structuredValueAsText(value, key);
    if (text && !chatMessages.has(text) && !chatMessages.has(scalarText(value) ?? '')) structured.push(text);
  }

  for (const [nodeId, nodeRun] of Object.entries(nodeRuns)) {
    collectArtifacts(nodeRun?.output, `node:${nodeId}`, artifacts);
  }

  visible.push(...dedupeAndApplySiblingPrecedence(artifacts));
  for (const text of structured) appendText(visible, text);

  if (visible.length === 0) return [{ kind: 'text', text: EMPTY_CHAT_RESULT }];
  return visible;
}