import type { RunDetail } from '../../../api/types';
import type { NodeStatus } from '../cockpit-state';
import type {
  GuidedStageSpec,
  GuidedVisibility,
  NodeExperienceSpec,
  YamlWorkflow,
  YamlWorkflowNode,
} from '../yaml-bridge';

export type GuidedStageState = 'planned' | 'active' | 'completed' | 'attention' | 'skipped';

export type GuidedStep = {
  id: string;
  displayName: string;
  stageId: string;
  purpose: string;
  contribution: string;
  expectedOutput: string;
  visibility: GuidedVisibility;
  role?: string;
  showRole: boolean;
  receivingSteps: string[];
  recoveryActions: string[];
  failureMessage?: string;
  status: NodeStatus;
  output: unknown;
  outcome: string;
  keyPoints: string[];
  qualitySummary: string;
};

export type GuidedStage = {
  id: string;
  displayName: string;
  purpose: string;
  successCriteria: string[];
  expectedOutput?: string;
  visibility: GuidedVisibility;
  weight: number;
  nodeIds: string[];
  state: GuidedStageState;
  completedCount: number;
  totalCount: number;
};

export type GuidedRuntimeModel = {
  goal: string;
  stages: GuidedStage[];
  steps: GuidedStep[];
  currentStage: GuidedStage | null;
  currentStep: GuidedStep | null;
  contributions: GuidedStep[];
  completedStageCount: number;
};

export type GuidedArtifact = {
  key: string;
  extension: string;
  label: string;
  nodeId: string;
};

const DEFAULT_STAGES: Array<GuidedStageSpec & { matcher: RegExp }> = [
  {
    id: 'prepare',
    display_name: 'Prepare',
    purpose: 'Check the files, inputs and settings needed for a reliable run.',
    expected_output: 'A ready-to-use set of inputs and assumptions.',
    matcher: /(^|_)(start|load|input|ingest|preflight|normalise|normalize|metadata|readiness)(_|$)/i,
  },
  {
    id: 'understand',
    display_name: 'Understand',
    purpose: 'Interpret the request, objectives, constraints and success criteria.',
    expected_output: 'A shared understanding of what the final result must achieve.',
    matcher: /(understand|interpret|requirement|call_intelligence|research_plan|scope|objective|brief)/i,
  },
  {
    id: 'gather',
    display_name: 'Gather',
    purpose: 'Find and organise the evidence, data and prior work needed for the result.',
    expected_output: 'A traceable evidence and source set for later work.',
    matcher: /(retriev|search|research|source|evidence|citation|dataset|database|literature|candidate)/i,
  },
  {
    id: 'create',
    display_name: 'Create',
    purpose: 'Produce the analysis, draft, plan, figures or other main deliverable.',
    expected_output: 'A complete working version of the requested deliverable.',
    matcher: /(draft|create|generate|compile|synthesi|methodology|blueprint|concept|figure|assemble)/i,
  },
  {
    id: 'check',
    display_name: 'Check',
    purpose: 'Review completeness, consistency, evidence coverage and quality.',
    expected_output: 'A checked result with gaps and review items clearly identified.',
    matcher: /(verify|review|evaluat|quality|consisten|coverage|red_team|peer_review|compliance|gate|truth_graph)/i,
  },
  {
    id: 'finalise',
    display_name: 'Finalise',
    purpose: 'Package the approved work and prepare the final deliverables.',
    expected_output: 'The final deliverable and its supporting files.',
    matcher: /(final|submission|render|package|export|docx|pdf|publish)/i,
  },
];

const TERMINAL_NODE_STATES = new Set<NodeStatus>([
  'done', 'reused', 'failed', 'skipped', 'cancelled',
]);

export const GUIDED_STATUS_LABEL: Record<NodeStatus, string> = {
  pending: 'Planned',
  active: 'Working',
  done: 'Completed',
  reused: 'Completed from saved work',
  paused: 'Waiting for you',
  failed: 'Needs attention',
  skipped: 'Not needed',
  cancelled: 'Stopped safely',
};

export function humanizeIdentifier(value: string): string {
  const spaced = value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_./-]+/g, ' ')
    .replace(/\b(agent|node)\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!spaced) return 'Workflow step';
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function compactText(value: unknown, maxLength = 180): string | null {
  if (typeof value !== 'string') return null;
  const text = value.replace(/\s+/g, ' ').trim();
  if (!text) return null;
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function keyPointsFromOutput(output: unknown): string[] {
  if (output == null) return [];
  if (Array.isArray(output)) {
    return [`Produced ${output.length} item${output.length === 1 ? '' : 's'}.`];
  }
  const record = asRecord(output);
  if (!record) {
    const text = compactText(output);
    return text ? [text] : [];
  }

  const points: string[] = [];
  for (const key of ['outcome', 'summary', 'answer', 'result', 'raw']) {
    const text = compactText(record[key]);
    if (text) {
      points.push(text);
      break;
    }
  }
  for (const [key, value] of Object.entries(record)) {
    if (points.length >= 3) break;
    if (['outcome', 'summary', 'answer', 'result', 'raw'].includes(key)) continue;
    const label = humanizeIdentifier(key);
    if (Array.isArray(value) && value.length > 0) {
      points.push(`${label}: ${value.length} item${value.length === 1 ? '' : 's'}.`);
    } else if (typeof value === 'number') {
      points.push(`${label}: ${value.toLocaleString()}.`);
    } else if (typeof value === 'boolean') {
      points.push(`${label}: ${value ? 'yes' : 'no'}.`);
    }
  }
  if (points.length === 0) {
    const fieldCount = Object.keys(record).length;
    if (fieldCount > 0) points.push(`Produced ${fieldCount} structured field${fieldCount === 1 ? '' : 's'}.`);
  }
  return points.slice(0, 3);
}

function qualitySummary(output: unknown): string {
  const record = asRecord(output);
  if (!record) return 'Available for the next stage; no separate quality result was reported.';
  const blocking = Array.isArray(record.blocking_issues) ? record.blocking_issues.length : 0;
  const warnings = Array.isArray(record.warnings) ? record.warnings.length : 0;
  const reviewItems = Array.isArray(record.review_items) ? record.review_items.length : 0;
  if (blocking > 0) return `${blocking} blocking issue${blocking === 1 ? '' : 's'} require attention.`;
  if (record.submission_ready === false) return 'Review is required before this result is ready to use.';
  if (warnings + reviewItems > 0) {
    const count = warnings + reviewItems;
    return `Completed with ${count} review item${count === 1 ? '' : 's'}.`;
  }
  if (record.submission_ready === true) return 'Required readiness checks were reported as passed.';
  return 'Available for the next stage; no separate quality result was reported.';
}

function inferredStageId(node: YamlWorkflowNode, index: number, total: number): string {
  const searchable = `${node.id} ${node.type}`;
  // Final packaging and checking terms are more specific than generic words
  // such as "load" or "research", so test them before the broad matchers.
  const precedence = ['finalise', 'check', 'prepare', 'understand', 'gather', 'create'];
  for (const id of precedence) {
    const stage = DEFAULT_STAGES.find(item => item.id === id)!;
    if (stage.matcher.test(searchable)) return id;
  }
  const fallbackIndex = Math.min(
    DEFAULT_STAGES.length - 1,
    Math.floor((index / Math.max(1, total)) * DEFAULT_STAGES.length),
  );
  return DEFAULT_STAGES[fallbackIndex].id;
}

function stageState(nodeIds: string[], statuses: Record<string, NodeStatus>): GuidedStageState {
  const values = nodeIds.map(id => statuses[id] ?? 'pending');
  if (values.some(status => status === 'failed')) return 'attention';
  if (values.some(status => status === 'active' || status === 'paused')) return 'active';
  if (values.length > 0 && values.every(status => TERMINAL_NODE_STATES.has(status))) {
    return values.every(status => status === 'skipped' || status === 'cancelled')
      ? 'skipped'
      : 'completed';
  }
  return 'planned';
}

function stageForNode(
  node: YamlWorkflowNode,
  explicitStages: GuidedStageSpec[],
  index: number,
  total: number,
): string {
  if (node.experience?.stage_id) return node.experience.stage_id;
  const explicit = explicitStages.find(stage => stage.node_ids?.includes(node.id));
  return explicit?.id ?? inferredStageId(node, index, total);
}

function fallbackExperience(node: YamlWorkflowNode, stage: GuidedStageSpec): Required<Pick<
  NodeExperienceSpec,
  'display_name' | 'purpose' | 'contribution' | 'expected_output'
>> {
  return {
    display_name: humanizeIdentifier(node.id),
    purpose: stage.purpose || `Complete the ${stage.display_name.toLowerCase()} work for this workflow.`,
    contribution: stage.expected_output
      ? `Contributes to ${stage.expected_output.charAt(0).toLowerCase()}${stage.expected_output.slice(1)}`
      : 'Provides a usable result for the work that follows.',
    expected_output: stage.expected_output || 'A structured result for the next step.',
  };
}

export function buildGuidedRuntimeModel({
  workflow,
  nodeStatuses,
  outputs,
  activeNodeId,
  gateNodeId,
}: {
  workflow: YamlWorkflow;
  nodeStatuses: Record<string, NodeStatus>;
  outputs: Record<string, unknown>;
  activeNodeId?: string | null;
  gateNodeId?: string | null;
}): GuidedRuntimeModel {
  const explicitStages = workflow.experience?.stages ?? [];
  const stageDefinitions = new Map<string, GuidedStageSpec>();
  for (const stage of DEFAULT_STAGES) stageDefinitions.set(stage.id, stage);
  for (const stage of explicitStages) stageDefinitions.set(stage.id, stage);

  const stageNodes = new Map<string, string[]>();
  const nodeStage = new Map<string, string>();
  workflow.nodes.forEach((node, index) => {
    const stageId = stageForNode(node, explicitStages, index, workflow.nodes.length);
    nodeStage.set(node.id, stageId);
    stageNodes.set(stageId, [...(stageNodes.get(stageId) ?? []), node.id]);
    if (!stageDefinitions.has(stageId)) {
      stageDefinitions.set(stageId, {
        id: stageId,
        display_name: humanizeIdentifier(stageId),
        purpose: 'Complete this part of the workflow.',
      });
    }
  });

  const orderedStageIds = [
    ...explicitStages.map(stage => stage.id),
    ...DEFAULT_STAGES.map(stage => stage.id),
    ...stageNodes.keys(),
  ].filter((id, index, values) => values.indexOf(id) === index && (stageNodes.get(id)?.length ?? 0) > 0);

  const stages: GuidedStage[] = orderedStageIds.map((id) => {
    const definition = stageDefinitions.get(id)!;
    const nodeIds = stageNodes.get(id) ?? [];
    const completedCount = nodeIds.filter(nodeId => (
      ['done', 'reused'].includes(nodeStatuses[nodeId] ?? 'pending')
    )).length;
    return {
      id,
      displayName: definition.display_name,
      purpose: definition.purpose ?? '',
      successCriteria: definition.success_criteria ?? [],
      expectedOutput: definition.expected_output,
      visibility: definition.visibility ?? 'standard',
      weight: definition.weight ?? 1,
      nodeIds,
      state: stageState(nodeIds, nodeStatuses),
      completedCount,
      totalCount: nodeIds.length,
    };
  });

  const stageById = new Map(stages.map(stage => [stage.id, stage]));
  const steps: GuidedStep[] = workflow.nodes.map((node) => {
    const stageId = nodeStage.get(node.id)!;
    const stage = stageById.get(stageId)!;
    const experience = node.experience ?? {};
    const fallback = fallbackExperience(node, {
      id: stage.id,
      display_name: stage.displayName,
      purpose: stage.purpose,
      expected_output: stage.expectedOutput,
    });
    const output = outputs[node.id];
    const points = keyPointsFromOutput(output);
    return {
      id: node.id,
      displayName: experience.display_name ?? fallback.display_name,
      stageId,
      purpose: experience.purpose ?? fallback.purpose,
      contribution: experience.contribution ?? fallback.contribution,
      expectedOutput: experience.expected_output ?? fallback.expected_output,
      visibility: experience.visibility ?? 'standard',
      role: experience.agent_role,
      showRole: Boolean(experience.show_agent_role && experience.agent_role),
      receivingSteps: experience.receiving_steps ?? [],
      recoveryActions: experience.recovery_actions ?? [],
      failureMessage: experience.failure_message,
      status: nodeStatuses[node.id] ?? 'pending',
      output,
      outcome: experience.expected_output
        ? `${experience.expected_output.replace(/[.]$/, '')}.`
        : points[0] ?? `${fallback.display_name} completed.`,
      keyPoints: points.slice(experience.expected_output ? 0 : 1, 4),
      qualitySummary: qualitySummary(output),
    };
  });

  const currentStage = stages.find(stage => stage.state === 'attention')
    ?? stages.find(stage => stage.state === 'active')
    ?? stages.find(stage => stage.state === 'planned')
    ?? stages.at(-1)
    ?? null;
  const visibleSteps = steps.filter(step => step.visibility !== 'advanced');
  const currentStep = visibleSteps.find(step => step.id === gateNodeId)
    ?? visibleSteps.find(step => step.id === activeNodeId)
    ?? visibleSteps.find(step => step.stageId === currentStage?.id && step.status === 'pending')
    ?? visibleSteps.find(step => step.stageId === currentStage?.id)
    ?? null;
  const contributions = visibleSteps.filter(step => step.status === 'done' || step.status === 'reused');

  return {
    goal: workflow.experience?.goal || workflow.description || `Complete ${humanizeIdentifier(workflow.name)}.`,
    stages,
    steps,
    currentStage,
    currentStep,
    contributions,
    completedStageCount: stages.filter(stage => stage.state === 'completed').length,
  };
}

export function collectGuidedArtifacts(outputs: Record<string, unknown>): GuidedArtifact[] {
  const seen = new Set<string>();
  const artifacts: GuidedArtifact[] = [];
  for (const [nodeId, output] of Object.entries(outputs)) {
    const record = asRecord(output);
    if (!record) continue;
    for (const [field, value] of Object.entries(record)) {
      if (typeof value !== 'string') continue;
      if (!(field.endsWith('_key') || field === 'minio_key')) continue;
      const extension = value.split('.').pop()?.toLowerCase() ?? '';
      if (!['pdf', 'docx', 'pptx', 'xlsx', 'html', 'csv', 'json', 'png', 'jpg', 'jpeg', 'webp'].includes(extension)) continue;
      if (seen.has(value)) continue;
      seen.add(value);
      artifacts.push({
        key: value,
        extension,
        label: `${extension.toUpperCase()} deliverable`,
        nodeId,
      });
    }
  }
  return artifacts;
}

export function nodeStatusesFromRun(
  workflow: YamlWorkflow,
  cockpitStates: Record<string, NodeStatus>,
  liveRun: RunDetail | null,
  finished: boolean,
): Record<string, NodeStatus> {
  return Object.fromEntries(workflow.nodes.map((node) => {
    const liveStatus = liveRun?.node_runs?.[node.id]?.status;
    const mappedLive: NodeStatus | undefined = liveStatus === 'running' ? 'active'
      : liveStatus === 'paused' ? 'paused'
      : liveStatus === 'completed' ? 'done'
      : liveStatus === 'reused' ? 'reused'
      : liveStatus === 'failed' ? 'failed'
      : undefined;
    const eventStatus = cockpitStates[node.id];
    const status = mappedLive
      ?? (eventStatus && eventStatus !== 'pending' ? eventStatus : undefined)
      ?? (node.id in (liveRun?.outputs ?? {}) ? 'done' : undefined)
      ?? (finished ? 'skipped' : 'pending');
    return [node.id, status];
  }));
}
