import { useState } from 'react';
import { api } from '../../../api/client';

const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;

export function ImportWorkflowDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (name: string) => void;
}) {
  const [name, setName] = useState('');
  const [yamlText, setYamlText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const nameValid = NAME_PATTERN.test(name);

  async function submit() {
    if (!nameValid || !yamlText.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      await api.saveWorkflow(name, yamlText);
      onImported(name);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="import-workflow-dialog">
        <div className="prepare-run-header">
          <div>
            <div className="library-details-eyebrow">Import workflow</div>
            <h2>Add a workflow from YAML</h2>
          </div>
          <button type="button" aria-label="Cancel" onClick={onClose}>×</button>
        </div>
        <div className="prepare-run-body">
          <label className="guided-builder-field">
            <span>File name</span>
            <input
              value={name}
              onChange={event => setName(event.target.value)}
              placeholder="my_new_workflow"
            />
            <small>Letters, numbers, underscores and hyphens only.</small>
          </label>
          <label className="guided-builder-field">
            <span>Workflow YAML</span>
            <textarea
              rows={14}
              value={yamlText}
              onChange={event => setYamlText(event.target.value)}
              placeholder="Paste the full workflow YAML here…"
              className="import-workflow-textarea"
            />
          </label>
          {error && <div className="library-details-error">{error}</div>}
          <div className="prepare-run-actions">
            <button type="button" className="ui-button ui-button--secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="ui-button ui-button--primary"
              disabled={!nameValid || !yamlText.trim() || saving}
              onClick={() => void submit()}
            >
              {saving ? 'Validating and importing…' : 'Import workflow'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
