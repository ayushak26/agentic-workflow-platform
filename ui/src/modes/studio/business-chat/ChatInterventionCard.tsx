import { useEffect, useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type { HITLReviewPanel, WorkflowFileCapabilities, WorkflowFileReference } from '../../../api/types';
import { plainTextJsonAdapter } from '../hitl-plain-text';
import type { DurableChatMessage } from './chatTranscript';

type InterventionMessage = Extract<DurableChatMessage, { role: 'intervention' }>;

const DEFAULT_CAPABILITIES: WorkflowFileCapabilities = {
  categories: {}, extensions: [],
  extractable_extensions: ['.pdf', '.docx', '.pptx', '.xlsx', '.txt', '.md', '.json', '.yaml', '.yml'],
  reference_only_extensions: [], max_file_size_bytes: 50 * 1024 * 1024, max_files_per_input: 20,
};

function reviewValue(value: unknown): string {
  if (value === null || value === undefined) return 'Not available';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function ReviewFact({ panel }: { panel: HITLReviewPanel }) {
  return <div className="chat-approval-fact"><strong>{panel.label}</strong><p>{panel.available ? reviewValue(panel.value) : 'Not available'}</p>{panel.hint && <small>{panel.hint}</small>}</div>;
}

export function ChatInterventionCard({ message, onResult }: {
  message: InterventionMessage;
  onResult: (result: unknown, decision: string) => void;
}) {
  const { request } = message;
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [sourceDocument, setSourceDocument] = useState<WorkflowFileReference | null>(null);
  const [capabilities, setCapabilities] = useState(DEFAULT_CAPABILITIES);
  const adapter = useMemo(() => request.content?.format === 'json' ? plainTextJsonAdapter(request.content.text) : null, [request.content]);
  const initialText = adapter?.displayText ?? request.content?.text ?? '';
  const [editedText, setEditedText] = useState(initialText);

  useEffect(() => { void api.workflowFileCapabilities().then(setCapabilities).catch(() => undefined); }, []);

  if (message.status === 'resolved') {
    return <div className="chat-approval-resolved">✓ Review resolved{message.resolution ? ` — ${message.resolution}` : ''}.</div>;
  }

  const canApprove = request.allowedActions.includes('approve');
  const canEdit = request.allowedActions.includes('edit');
  const canReject = request.allowedActions.includes('reject');
  const dirty = canEdit && (editedText !== initialText || sourceDocument !== null);
  const overLimit = (adapter?.serialize(editedText) ?? editedText).length > request.maxEditChars;
  const accept = capabilities.extractable_extensions.join(',');

  async function decide(action: 'approve' | 'reject' | 'edit') {
    if (busy || uploading || (action === 'approve' && !canApprove) || (action === 'edit' && (!canEdit || overLimit)) || (action === 'reject' && !canReject)) return;
    setBusy(true); setError(null);
    try {
      const payload: Record<string, unknown> = { decision: action };
      if (action === 'reject' && reason.trim()) payload.reason = reason.trim();
      if (action === 'edit') payload.edited_content = {
        text: adapter?.serialize(editedText) ?? editedText,
        html: null,
        format: request.content?.format ?? 'text',
        source: sourceDocument ? 'upload' : 'editor',
        source_document: sourceDocument,
      };
      const result = await api.resumeWorkflow(request.runId, payload);
      onResult(result, action);
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    } finally {
      setBusy(false);
    }
  }

  async function replaceWithDocument(file: File) {
    if (!canEdit || !request.allowDocumentOverride || busy || uploading) return;
    setUploading(true); setError(null);
    try {
      const uploaded = await api.uploadWorkflowFiles([file]);
      const ref = uploaded.files[0];
      const extracted = await api.extractWorkflowFile(ref, request.maxEditChars);
      setEditedText(extracted.text);
      setSourceDocument(ref);
      if (extracted.truncated) setError(`Only the first ${request.maxEditChars.toLocaleString()} characters were loaded from ${ref.name}.`);
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="chat-approval-card" aria-label={`Approval required: ${request.displayName}`}>
      <header><span>Action required — {request.displayName}</span><em>No external action has been taken</em></header>
      <div className="chat-approval-body">
        <div className="chat-approval-intro"><span aria-hidden>!</span><div><h3>{request.question}</h3>{request.reviewPurpose && <p>{request.reviewPurpose}</p>}<small>Review gate · {request.parentRunId ? 'Subworkflow action' : 'Workflow action'}</small></div></div>
        {request.panels.length > 0
          ? <div className="chat-approval-facts">{request.panels.map(panel => <ReviewFact key={`${panel.field}:${panel.label}`} panel={panel} />)}</div>
          : Object.keys(request.context).length > 0
            ? <div className="chat-approval-context">{reviewValue(request.context)}</div>
            : null}
        {canEdit && <div className="chat-approval-editor">
          <div><label htmlFor={`review-${request.gateId}`}>Edit before continuing</label>{request.allowDocumentOverride && <label className="chat-approval-replace">{uploading ? 'Replacing…' : 'Replace with document'}<input className="sr-only" type="file" accept={accept} disabled={busy || uploading} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void replaceWithDocument(file); }} /></label>}</div>
          <textarea id={`review-${request.gateId}`} rows={8} value={editedText} onChange={event => { setEditedText(event.target.value); setSourceDocument(null); }} />
          <footer><span className={overLimit ? 'is-over-limit' : ''}>{(adapter?.serialize(editedText) ?? editedText).length.toLocaleString()} / {request.maxEditChars.toLocaleString()} characters</span>{dirty && <button type="button" disabled={busy || uploading} onClick={() => { setEditedText(initialText); setSourceDocument(null); setError(null); }}>Discard changes</button>}</footer>
        </div>}
        {canReject && <label className="chat-approval-reason">Reason for rejection <span>optional</span><input value={reason} onChange={event => setReason(event.target.value)} placeholder="Optional rejection reason" /></label>}
        <div className="chat-approval-actions">
          {dirty
            ? <button type="button" disabled={busy || uploading || overLimit} onClick={() => void decide('edit')} className="is-primary">{busy ? 'Continuing…' : 'Save changes and continue'}</button>
            : canApprove
              ? <button type="button" disabled={busy || uploading} onClick={() => void decide('approve')} className="is-primary">{busy ? 'Continuing…' : 'Approve and continue'}</button>
              : null}
          {canReject && <button type="button" disabled={busy || uploading} onClick={() => void decide('reject')}>Reject</button>}
        </div>
        {error && <div className="chat-approval-error" role="alert">{error}</div>}
      </div>
    </section>
  );
}