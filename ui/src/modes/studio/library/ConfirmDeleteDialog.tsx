import { useState } from 'react';
import type { WorkflowSummary } from '../../../api/types';
import { humanizeIdentifier } from '../guided/runtime-model';

export function ConfirmDeleteDialog({
  workflow,
  onCancel,
  onConfirm,
}: {
  workflow: WorkflowSummary;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const title = workflow.library?.title || humanizeIdentifier(workflow.name);

  async function confirm() {
    setDeleting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setDeleting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="confirm-delete-dialog">
        <div className="prepare-run-header">
          <div>
            <div className="library-details-eyebrow">Delete workflow</div>
            <h2>{title}</h2>
          </div>
          <button type="button" aria-label="Cancel" onClick={onCancel}>×</button>
        </div>
        <div className="prepare-run-body">
          <p>
            This permanently deletes <strong>{workflow.name}.yaml</strong> along
            with its entire saved version history. This cannot be undone.
          </p>
          {workflow.library?.visibility_status === 'approved' && (
            <div className="library-readiness-banner is-ready_with_warnings">
              <strong>This workflow is marked Approved.</strong>
              <p>Anyone relying on it as a standard workflow will lose access.</p>
            </div>
          )}
          {error && <div className="library-details-error">{error}</div>}
          <div className="prepare-run-actions">
            <button type="button" className="ui-button ui-button--secondary" onClick={onCancel} disabled={deleting}>
              Cancel
            </button>
            <button
              type="button"
              className="ui-button ui-button--danger"
              onClick={() => void confirm()}
              disabled={deleting}
            >
              {deleting ? 'Deleting…' : 'Delete permanently'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
