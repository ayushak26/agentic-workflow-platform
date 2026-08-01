import type { NodeProps } from 'reactflow';

export const STAGE_BAND_TYPE = 'stageBand';

export type StageBandData = {
  label: string;
  width: number;
  height: number;
};

/**
 * A background rectangle + label marking one stage's column, rendered
 * behind the real nodes (given a lower zIndex on the node itself — see
 * where these are constructed) so parallel branches read as visually
 * grouped without needing a real ReactFlow parent/child node hierarchy.
 * Never interactive — no handles, not selectable, not draggable.
 */
export function StageBandNode({ data }: NodeProps<StageBandData>) {
  return (
    <div
      className="rounded-lg bg-slate-50/70 border border-slate-200"
      style={{ width: data.width, height: data.height }}
    >
      <div className="px-2.5 py-1 text-[10px] uppercase tracking-wide text-ink-400 font-medium">
        {data.label}
      </div>
    </div>
  );
}
