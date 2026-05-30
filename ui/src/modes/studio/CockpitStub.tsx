// CockpitStub.tsx
import { useParams } from 'react-router-dom';
export function CockpitStub() {
  const { runId } = useParams();
  return (
    <div className="p-8">
      <h2 className="text-xl font-semibold">Cockpit</h2>
      <p className="text-ink-500 mt-2">
        Live execution view for run {runId}. Lands in Phase 9B.3.
      </p>
    </div>
  );
}