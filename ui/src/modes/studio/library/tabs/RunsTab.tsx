import type { WorkflowStats } from '../../../../api/types';
import { Spinner } from '../../../../components/Spinner';

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes} min ${remaining.toString().padStart(2, '0')} sec`;
}

function relativeDate(iso: string | null): string {
  if (!iso) return 'Never';
  return new Date(iso).toLocaleString();
}

export function RunsTab({ stats, error }: { stats: WorkflowStats | null; error: string | null }) {
  if (error) {
    return <div className="library-tab-content"><div className="library-details-error">Could not load run history: {error}</div></div>;
  }
  if (!stats) {
    return <div className="library-tab-content"><Spinner label="Loading run history…" /></div>;
  }

  return (
    <div className="library-tab-content">
      <div className="library-runs-facts">
        <div>
          <span>Last run</span>
          <strong>{relativeDate(stats.last_run_at)}</strong>
          {stats.last_run_status && <p>Status: {stats.last_run_status}</p>}
        </div>
        <div>
          <span>Last successful run</span>
          <strong>{relativeDate(stats.last_successful_run_at)}</strong>
        </div>
        <div>
          <span>Runs recorded (this account)</span>
          <strong>{stats.sample_size}</strong>
        </div>
      </div>

      {stats.enough_data_for_estimates ? (
        <div className="library-runs-facts">
          <div>
            <span>Success rate</span>
            <strong>{Math.round((stats.success_rate ?? 0) * 100)}%</strong>
          </div>
          <div>
            <span>Median duration</span>
            <strong>{stats.median_duration_s != null ? formatDuration(stats.median_duration_s) : 'Unknown'}</strong>
          </div>
          {stats.most_common_failure && (
            <div>
              <span>Most common blocker</span>
              <strong>{stats.most_common_failure}</strong>
            </div>
          )}
        </div>
      ) : (
        <div className="library-empty-note">
          Not enough completed runs to estimate duration or success rate reliably yet
          ({stats.sample_size} recorded so far).
        </div>
      )}

      <div className="library-empty-note">
        These numbers reflect only runs launched from your account — Run
        History has the full detail for each one.
      </div>
    </div>
  );
}
