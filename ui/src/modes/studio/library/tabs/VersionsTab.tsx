import type { WorkflowVersionSummary } from '../../../../api/types';
import { Spinner } from '../../../../components/Spinner';

export function VersionsTab({
  versions,
  error,
  onOpenBuilder,
}: {
  versions: WorkflowVersionSummary[] | null;
  error: string | null;
  onOpenBuilder: () => void;
}) {
  if (error) {
    return <div className="library-tab-content"><div className="library-details-error">Could not load version history: {error}</div></div>;
  }
  if (!versions) {
    return <div className="library-tab-content"><Spinner label="Loading versions…" /></div>;
  }
  if (versions.length === 0) {
    return (
      <div className="library-tab-content">
        <div className="library-empty-note">
          This workflow has no saved version history yet — every manual save
          made from the Builder creates one.
        </div>
      </div>
    );
  }

  return (
    <div className="library-tab-content">
      <ul className="library-version-list">
        {versions.map(version => (
          <li key={version.version_id} className={version.current ? 'is-current' : ''}>
            <div className="library-version-row">
              <strong>{new Date(version.created_at).toLocaleString()}</strong>
              {version.current && <span className="library-version-badge">Current</span>}
            </div>
            <div className="library-version-meta">
              {version.node_count} nodes · workflow v{version.workflow_version}
            </div>
            {version.description && <p>{version.description}</p>}
          </li>
        ))}
      </ul>
      <button type="button" className="ui-button ui-button--secondary" onClick={onOpenBuilder}>
        Manage versions in Builder
      </button>
      <div className="library-empty-note">
        Restoring an earlier version, or comparing two versions in detail,
        happens in the Builder — it preflights a restore before it takes
        effect, and every run stays tied to the exact version it used.
      </div>
    </div>
  );
}
