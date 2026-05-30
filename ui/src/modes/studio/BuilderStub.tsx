// BuilderStub.tsx
import { useParams } from 'react-router-dom';
export function BuilderStub() {
  const { name } = useParams();
  return (
    <div className="p-8">
      <h2 className="text-xl font-semibold">Builder</h2>
      <p className="text-ink-500 mt-2">
        {name ? `Will edit workflow "${name}".` : 'Will create a new workflow.'}
        {' '}React Flow canvas lands in Phase 9B.2.
      </p>
    </div>
  );
}