import { NodeResizer, type NodeProps } from 'reactflow';

import type { WorkflowNodeData } from '../yaml-bridge';

/**
 * A sticky note on the canvas: a personal annotation the author leaves for
 * themselves, never sent to the backend and never part of the graph (see
 * Builder.tsx's note handling — it lives in the same react-flow `nodes`
 * array as real steps so drag/select/delete/undo all work for free, but is
 * filtered out everywhere the workflow itself is built or compiled).
 *
 * Yellow is deliberate, not a default that happened to stick — it is the one
 * node on the canvas that is never a workflow step, and the color is what
 * makes that obvious at a glance.
 */

export const NOTE_NODE_TYPE = 'note';
export const NOTE_ID_PREFIX = 'note-';
export const isNoteNodeId = (id: string): boolean => id.startsWith(NOTE_ID_PREFIX);

export function NoteNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      className={`group relative h-full w-full rounded-md border p-2 shadow-sm ${
        selected ? 'border-amber-500 ring-2 ring-amber-300' : 'border-amber-300'
      }`}
      style={{ background: '#fef9c3' }}
    >
      <NodeResizer isVisible={selected} minHeight={90} minWidth={140} />
      <button
        aria-label="Delete note"
        className="nodrag absolute -right-2 -top-2 hidden h-5 w-5 items-center justify-center rounded-full border border-amber-400 bg-white text-[11px] font-semibold text-amber-700 shadow-sm hover:bg-amber-50 group-hover:flex"
        onClick={data.onNoteDelete}
        title="Delete note"
        type="button"
      >×</button>
      <textarea
        className="nodrag nowheel h-full w-full resize-none bg-transparent text-[12px] leading-4 text-amber-950 placeholder:text-amber-700/60 focus:outline-none"
        onChange={event => data.onNoteChange?.(event.target.value)}
        placeholder="Note to self…"
        value={data.noteText ?? ''}
      />
    </div>
  );
}
