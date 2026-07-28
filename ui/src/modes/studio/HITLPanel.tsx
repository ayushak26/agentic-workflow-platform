import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type {
  HITLReviewContent,
  WorkflowFileCapabilities,
  WorkflowFileReference,
} from '../../api/types';
import {
  RichTextEditor,
  type RichEditorValue,
} from './RichTextEditor';

const DEFAULT_CAPABILITIES: WorkflowFileCapabilities = {
  categories: {},
  extensions: [],
  extractable_extensions: [
    '.pdf', '.docx', '.pptx', '.xlsx', '.txt', '.md', '.markdown',
    '.py', '.js', '.ts', '.tsx', '.json', '.yaml', '.yml',
  ],
  reference_only_extensions: [],
  max_file_size_bytes: 50 * 1024 * 1024,
  max_files_per_input: 20,
};

function emptyEditorValue(text = ''): RichEditorValue {
  return { text, html: '' };
}

function contentAsText(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2) ?? String(value);
}

function initialReviewContent(
  content: HITLReviewContent | null,
  context: unknown,
): HITLReviewContent {
  if (content && typeof content.text === 'string') return content;

  if (context && typeof context === 'object' && !Array.isArray(context)) {
    const first = Object.entries(context as Record<string, unknown>)
      .find(([, value]) => value !== null && value !== undefined);
    if (first) {
      const [sourcePath, value] = first;
      return {
        text: contentAsText(value),
        format: typeof value === 'string' ? 'text' : 'json',
        source: 'workflow',
        source_path: sourcePath,
      };
    }
  }

  const fallback = context ?? '';
  return {
    text: contentAsText(fallback),
    format: typeof fallback === 'string' ? 'text' : 'json',
    source: 'workflow',
  };
}

function formatBytes(value: number): string {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(0)} MB`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function HITLPanel({
  runId,
  pausedNodeId,
  question,
  context,
  allowedActions,
  content,
  allowDocumentOverride,
  maxEditChars,
  onResult,
}: {
  runId: string;
  pausedNodeId: string;
  question: string;
  context: unknown;
  allowedActions: string[];
  content: HITLReviewContent | null;
  allowDocumentOverride: boolean;
  maxEditChars: number;
  onResult: (result: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [capabilities, setCapabilities] = useState(DEFAULT_CAPABILITIES);
  const initialContent = useMemo(
    () => initialReviewContent(content, context),
    [content, context],
  );
  const [editorValue, setEditorValue] = useState<RichEditorValue>(
    () => emptyEditorValue(initialContent.text),
  );
  const [editorFormat, setEditorFormat] = useState<'text' | 'json'>(
    initialContent.format ?? 'text',
  );
  const [editorRevision, setEditorRevision] = useState(0);
  const [sourceDocument, setSourceDocument] =
    useState<WorkflowFileReference | null>(null);
  const [overrideTruncated, setOverrideTruncated] = useState(false);

  useEffect(() => {
    api.workflowFileCapabilities()
      .then(setCapabilities)
      .catch(() => undefined);
  }, []);

  const canEdit = allowedActions.includes('edit');
  const canReject = allowedActions.includes('reject');
  const originalText = initialContent.text;
  const dirty = (
    editorValue.text !== originalText
    || sourceDocument !== null
  );
  const charCount = editorValue.text.length;
  const wordCount = useMemo(
    () => editorValue.text.trim().split(/\s+/).filter(Boolean).length,
    [editorValue.text],
  );

  async function submit(action: 'approve' | 'reject' | 'edit') {
    if (busy) return; // guard against double-fire
    if (action === 'edit' && !canEdit) return;
    if (action === 'edit' && charCount > maxEditChars) {
      setError(`The edited content exceeds ${maxEditChars.toLocaleString()} characters.`);
      return;
    }
    if (action === 'edit' && editorFormat === 'json') {
      try {
        JSON.parse(editorValue.text);
      } catch {
        setError(
          'This review contains structured JSON. Fix the JSON syntax before continuing.',
        );
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { decision: action };
      if (action === 'reject' && reason) payload.reason = reason;
      if (action === 'edit') {
        payload.edited_content = {
          text: editorValue.text,
          html: editorValue.html,
          format: editorFormat,
          source: sourceDocument ? 'upload' : 'editor',
          source_document: sourceDocument,
        };
      }
      const result = await api.resumeWorkflow(runId, payload);
      onResult(result); // hand the next state up to the Cockpit
    } catch (e: unknown) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function overrideWithDocument(file: File) {
    if (!canEdit || !allowDocumentOverride || uploading) return;
    setUploading(true);
    setError(null);
    setOverrideTruncated(false);
    try {
      const uploaded = await api.uploadWorkflowFiles([file]);
      const ref = uploaded.files[0];
      const extracted = await api.extractWorkflowFile(ref, maxEditChars);
      setEditorValue(emptyEditorValue(extracted.text));
      setSourceDocument(ref);
      setOverrideTruncated(extracted.truncated);
      setEditorRevision((value) => value + 1);
    } catch (e: unknown) {
      setError(errorMessage(e));
    } finally {
      setUploading(false);
    }
  }

  function discardChanges() {
    setEditorValue(emptyEditorValue(originalText));
    setEditorFormat(initialContent.format ?? 'text');
    setSourceDocument(null);
    setOverrideTruncated(false);
    setEditorRevision((value) => value + 1);
    setError(null);
  }

  const accept = capabilities.extractable_extensions.join(',');

  return (
    <div className="p-6 pb-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="inline-block text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 bg-warn text-white">
            Action required
          </div>
          <h3 className="text-lg font-semibold mt-3">{pausedNodeId} is paused</h3>
          <p className="text-sm text-ink-700 mt-2">
            {question || 'Review this content before the workflow continues.'}
          </p>
        </div>
        <div className="shrink-0 rounded-lg bg-slate-50 px-3 py-2 text-right">
          <div className="text-xs font-medium text-ink-700">
            {wordCount.toLocaleString()} words
          </div>
          <div className={`text-[11px] ${charCount > maxEditChars ? 'text-bad' : 'text-ink-300'}`}>
            {charCount.toLocaleString()} / {maxEditChars.toLocaleString()} chars
          </div>
        </div>
      </div>

      <div className="mt-5">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div>
            <div className="text-xs font-medium text-ink-700">Review content</div>
            <div className="text-[11px] text-ink-300">
              {canEdit
                ? editorFormat === 'json'
                  ? 'Exact structured content. Keep the JSON valid so downstream fields preserve their types.'
                  : 'Click anywhere to edit. Formatting is preserved in the human decision.'
                : 'This gate is configured as read-only.'}
            </div>
          </div>
          {canEdit && allowDocumentOverride && (
            <label className="cursor-pointer rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-ink-700 hover:bg-slate-50">
              {uploading ? 'Replacing…' : 'Replace with document'}
              <input
                type="file"
                className="hidden"
                disabled={uploading || busy}
                accept={accept}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (file) void overrideWithDocument(file);
                }}
              />
            </label>
          )}
        </div>

        <RichTextEditor
          initialText={editorValue.text}
          resetKey={editorRevision}
          disabled={!canEdit || busy}
          onChange={setEditorValue}
        />
      </div>

      {sourceDocument && (
        <div className="mt-3 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2.5 text-xs text-cyan-900">
          <div className="font-medium">
            {sourceDocument.name} has replaced the previous editor content.
          </div>
          <div className="mt-1 text-cyan-700">
            {formatBytes(sourceDocument.size_bytes)} · the original workflow content
            remains unchanged until you click “Save changes and continue”.
          </div>
          {overrideTruncated && (
            <div className="mt-1 font-medium text-warn">
              This document was longer than the editor limit; only the first
              {' '}{maxEditChars.toLocaleString()} characters were loaded.
            </div>
          )}
        </div>
      )}

      <div className="mt-6 space-y-3">
        <button
          onClick={() => submit(dirty ? 'edit' : 'approve')}
          disabled={busy || uploading || charCount > maxEditChars}
          className="w-full px-4 py-2.5 rounded-md bg-ok text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {busy
            ? 'Working…'
            : dirty
              ? 'Save changes and continue'
              : 'Approve and continue'}
        </button>

        {dirty && (
          <button
            onClick={discardChanges}
            disabled={busy || uploading}
            className="w-full px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Discard changes
          </button>
        )}

        {canReject && (
          <>
            <div>
              <label className="block text-xs font-medium text-ink-700 mb-1">Rejection reason (optional)</label>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why are you rejecting?"
                className="block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border"
              />
            </div>
            <button
              onClick={() => submit('reject')}
              disabled={busy || uploading}
              className="w-full px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              Reject
            </button>
          </>
        )}
      </div>

      <details className="mt-6">
        <summary className="text-xs font-medium text-ink-700 cursor-pointer">
          Supporting pause context
        </summary>
        <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-3 mt-2 overflow-x-auto max-h-80 whitespace-pre-wrap">
{JSON.stringify(context, null, 2)}
        </pre>
      </details>

      {error && <div className="mt-3 text-sm text-bad">{error}</div>}
    </div>
  );
}
