import { useCallback, useState } from 'react';

import { api } from '../../../api/client';
import type { BusinessAction, BusinessActionResult } from '../../../api/types';

/**
 * Running a typed Business View action.
 *
 * A button never carries a URL or a prompt — it carries a `BusinessActionType`
 * that the server already decided is valid for this state and this person
 * (§53, §54). This hook is the single place that maps each type to how it is
 * carried out, in three groups:
 *
 *   - **Ask first.** Anything that needs a value from the person (a name, a
 *     note, a route, a corrected fact) opens a form before anything happens.
 *   - **Existing endpoint.** Pause, resume, stop, assign and the two rerun
 *     primitives each already have an audited route; they are called directly
 *     rather than re-implemented behind a second door.
 *   - **Dispatch.** Notes, route overrides, clarification drafts and record
 *     lookups go to `/business-action`, which re-checks permission server-side.
 *
 * A type this hook does not know is not run at all. It cannot be: the union is
 * closed and the server rejects anything outside it.
 */

export type ActionPrompt =
  | { kind: 'assign'; action: BusinessAction; suggested: string }
  | { kind: 'note'; action: BusinessAction }
  | { kind: 'route_override'; action: BusinessAction; current: string }
  | { kind: 'edit_fact'; action: BusinessAction; field: string; label: string; value: unknown };

export type ActionOutcome =
  | { kind: 'result'; result: BusinessActionResult }
  | { kind: 'message'; text: string };

export interface BusinessActionHandlers {
  /** Open the HITL review panel. */
  onReview: () => void;
  /** Open the technical drawer for one activity (or "run"). */
  onTechnical: (activityId: string) => void;
  /** Open the conversation, optionally pre-filled. */
  onAsk: (question?: string) => void;
  /** Open an attached file. */
  onOpenAttachment: (fileKey: string) => void;
  /** Start a retry or restart run and navigate to it. */
  onRerun: (mode: string) => void;
  /** Stop (and delete) this work item. */
  onStop: () => void;
  /** Resume a paused run. */
  onResume: () => void;
  /** Re-read the projection after something changed it. */
  onChanged: () => void;
}

export function useBusinessActions(runId: string | undefined, handlers: BusinessActionHandlers) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<ActionPrompt | null>(null);
  const [outcome, setOutcome] = useState<ActionOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dispatch = useCallback(
    async (action: BusinessAction, params: Record<string, unknown>) => {
      if (!runId) return;
      setBusyId(action.id);
      setError(null);
      try {
        const result = await api.businessAction(runId, action.type, params);
        setOutcome({ kind: 'result', result });
        handlers.onChanged();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [runId, handlers],
  );

  const run = useCallback(
    async (action: BusinessAction) => {
      if (!runId || !action.enabled) return;

      switch (action.type) {
        // ---- performed by the app itself
        case 'open_technical_details':
          handlers.onTechnical(String(action.params.activity_id ?? 'run'));
          return;
        case 'document_review':
          handlers.onOpenAttachment(String(action.params.file_key ?? ''));
          return;
        case 'open_related_record':
          handlers.onAsk(`Show ${action.params.reference}`);
          return;
        case 'ask_ai':
          handlers.onAsk(String(action.params.question ?? ''));
          return;
        case 'approve':
        case 'reject':
          handlers.onReview();
          return;
        case 'rerun_dependency':
          handlers.onRerun(String(action.params.mode ?? 'restart'));
          return;
        case 'stop_run':
          handlers.onStop();
          return;
        case 'resume_run':
          handlers.onResume();
          return;

        // ---- needs a value from the person before anything happens
        case 'assign_work_item':
          setPrompt({ kind: 'assign', action, suggested: String(action.params.suggested ?? '') });
          return;
        case 'add_note':
          setPrompt({ kind: 'note', action });
          return;
        case 'route_override':
          setPrompt({ kind: 'route_override', action, current: String(action.params.current ?? '') });
          return;
        case 'edit_fact':
          // The caller supplies the field's current value; this path is only
          // reached from a button that is not attached to one.
          setPrompt({
            kind: 'edit_fact',
            action,
            field: String(action.params.field ?? ''),
            label: String(action.params.field ?? ''),
            value: '',
          });
          return;

        // ---- existing audited endpoint
        case 'pause_run':
          setBusyId(action.id);
          setError(null);
          try {
            await api.pauseRun(runId);
            setOutcome({ kind: 'message', text: 'Paused after the current step.' });
            handlers.onChanged();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusyId(null);
          }
          return;

        // ---- typed dispatch
        case 'draft_clarification':
          await dispatch(action, { topic: action.params.topic });
          return;
        case 'related_record_lookup':
          await dispatch(action, action.params);
          return;
        case 'explain_decision':
          // Handled by the decision card, which owns the panel it opens into.
          return;
      }
    },
    [runId, handlers, dispatch],
  );

  /** Complete a prompted action with the value the person supplied. */
  const submitPrompt = useCallback(
    async (values: Record<string, string>) => {
      if (!runId || !prompt) return;
      setBusyId(prompt.action.id);
      setError(null);
      try {
        if (prompt.kind === 'assign') {
          await api.assignRun(runId, values.assignee);
          setOutcome({ kind: 'message', text: `Assigned to ${values.assignee}.` });
        } else if (prompt.kind === 'note') {
          await api.businessAction(runId, 'add_note', { text: values.text });
          setOutcome({ kind: 'message', text: 'Note added.' });
        } else if (prompt.kind === 'route_override') {
          await api.businessAction(runId, 'route_override', {
            route: values.route,
            reason: values.reason,
          });
          setOutcome({ kind: 'message', text: `Route changed to ${values.route}.` });
        } else {
          await api.correctFact(runId, prompt.field, coerce(prompt.value, values.value));
          setOutcome({ kind: 'message', text: `${prompt.label} updated.` });
        }
        setPrompt(null);
        handlers.onChanged();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [runId, prompt, handlers],
  );

  return {
    run,
    busyId,
    prompt,
    setPrompt,
    submitPrompt,
    outcome,
    clearOutcome: useCallback(() => setOutcome(null), []),
    error,
    clearError: useCallback(() => setError(null), []),
    /** Start editing a specific fact, with its current value pre-filled. */
    editFact: useCallback((action: BusinessAction, field: string, label: string, value: unknown) => {
      setPrompt({ kind: 'edit_fact', action, field, label, value });
    }, []),
  };
}

/**
 * Keep a corrected value the same shape the extraction produced.
 *
 * A quantity typed into a box arrives as a string; if the original was a
 * number or a list, storing the string would quietly break every rule that
 * reads it. An empty box means "not stated", which is null — not `""`.
 */
export function coerce(original: unknown, typed: string): unknown {
  const trimmed = typed.trim();
  if (typeof original === 'boolean') return trimmed.toLowerCase() === 'true' || trimmed.toLowerCase() === 'yes';
  if (Array.isArray(original)) return trimmed ? trimmed.split(',').map(part => part.trim()).filter(Boolean) : [];
  if (typeof original === 'number') {
    const parsed = Number(trimmed);
    return trimmed && !Number.isNaN(parsed) ? parsed : null;
  }
  return trimmed === '' ? null : trimmed;
}
