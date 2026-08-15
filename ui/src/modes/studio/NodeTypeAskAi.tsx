import { AskAiDialog } from './builder/AskAiDialog';

function starterQuestion(typeName: string): string {
  return `Explain the ${typeName} node — what it does, when to use it, and its advantages.`;
}

/** Thin wrapper over the shared AskAiDialog, scoped to one node type — kept
 *  as its own component because its two call sites (NodePalette.tsx,
 *  cockpit/NodeInspector.tsx) only ever need `typeName`. */
export function NodeTypeAskAi({ typeName, onClose }: { typeName: string; onClose: () => void }) {
  return (
    <AskAiDialog
      title={`Ask AI — ${typeName}`}
      starterQuestion={starterQuestion(typeName)}
      context={{ node_type: typeName }}
      onClose={onClose}
    />
  );
}
