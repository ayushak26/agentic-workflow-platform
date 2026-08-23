import type { NodeRun } from '../../../api/types';
import { WorkflowStepInspector } from '../../../components/workflows/WorkflowStepInspector';
import { workflowStatusFromNode } from '../../../components/workflows/workflowStatus';
import type { WorkflowChatNode } from './businessChatModel';

export function ChatNodeInspector({
  node,
  nodeRun,
  onClose,
}: {
  node: WorkflowChatNode;
  nodeRun?: NodeRun;
  onClose: () => void;
}) {
  return <WorkflowStepInspector step={{
    id: node.id,
    name: node.displayName,
    type: node.type,
    status: workflowStatusFromNode(nodeRun?.status),
    purpose: node.purpose,
    input: nodeRun?.input,
    output: nodeRun?.output,
    instructions: node.config,
    error: nodeRun?.error,
    durationSeconds: nodeRun?.duration_s,
    metadata: nodeRun?.model_selections,
  }} onClose={onClose} />;
}