import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { NodeTypeManifest } from '../../api/types';
import { Spinner } from '../../components/Spinner';

export function NodePalette() {
  const [types, setTypes] = useState<NodeTypeManifest[] | null>(null);

  useEffect(() => {
    api.nodeTypes().then(setTypes).catch(console.error);
  }, []);

  if (!types) return <div className="p-4"><Spinner /></div>;

  return (
    <div className="p-3 space-y-1">
      <div className="text-xs uppercase tracking-wide text-ink-500 px-2 pb-2">
        Node types
      </div>
      {types.map(t => (
        <div
          key={t.type_name}
          draggable
          onDragStart={e => {
            // React Flow checks this exact MIME type on drop.
            e.dataTransfer.setData('application/reactflow', t.type_name);
            e.dataTransfer.effectAllowed = 'move';
          }}
          className="rounded-md border border-slate-200 bg-white px-3 py-2 cursor-grab hover:border-accent-600 hover:shadow-sm transition"
          title={t.description}
        >
          <div className="text-sm font-medium text-ink-900">{t.type_name}</div>
          <div className="text-xs text-ink-500 line-clamp-2 mt-0.5">
            {t.description}
          </div>
        </div>
      ))}
    </div>
  );
}