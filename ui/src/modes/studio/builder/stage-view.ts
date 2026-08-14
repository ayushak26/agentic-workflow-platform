import type { Edge, Node } from 'reactflow';
import type { Stage } from '../flow-layout';
import type { WorkflowEdgeData, WorkflowNodeData } from '../yaml-bridge';

/**
 * Stage bands and stage collapse for the Builder canvas.
 *
 * A long workflow is long because it fans out: a stage with six parallel
 * branches costs six nodes' worth of canvas but carries one idea. Banding the
 * columns and letting a stage collapse to a single box is what turns a
 * fifty-node graph into a nine-stage graph you can take in at once.
 *
 * The Cockpit does the same thing for a *run* (cockpit/graph-collapse.ts),
 * where a collapsed stage has to summarise per-node run status. Here there is
 * no run: a collapsed stage summarises what the steps are, and both the band
 * and the placeholder are interactive so the structure can be opened and closed
 * while editing. Different data, different affordances — hence a separate
 * module rather than a shared one bent to cover both.
 *
 * Both functions are pure and operate on already-positioned nodes, so they can
 * be recomputed on every render without ever re-running dagre.
 */

export const BUILDER_STAGE_BAND_TYPE = 'builderStageBand';
export const BUILDER_STAGE_PLACEHOLDER_TYPE = 'builderStagePlaceholder';

/** Prefix marking a node the Builder draws but does not own — never in the YAML. */
export const SYNTHETIC_PREFIX = '__stage_';

const BAND_PADDING = 26;
const BAND_LABEL_HEADROOM = 30;
const PLACEHOLDER_WIDTH = 240;
const PLACEHOLDER_HEIGHT = 84;

export function isSyntheticNodeId(id: string): boolean {
  return id.startsWith(SYNTHETIC_PREFIX);
}

export function bandId(stageIndex: number): string {
  return `${SYNTHETIC_PREFIX}band_${stageIndex}__`;
}

export function placeholderId(stageIndex: number): string {
  return `${SYNTHETIC_PREFIX}placeholder_${stageIndex}__`;
}

export type BuilderStageBandData = {
  stageIndex: number;
  label: string;
  width: number;
  height: number;
  stepCount: number;
  onCollapse: (stageIndex: number) => void;
};

export type BuilderStagePlaceholderData = {
  stageIndex: number;
  label: string;
  nodeIds: string[];
  stepLabels: string[];
  hasIssue: boolean;
  onExpand: (stageIndex: number) => void;
};

/**
 * A background column per expanded stage, behind the real nodes (negative
 * zIndex rather than array order, which React Flow does not honour for
 * painting). A collapsed stage gets no band — its placeholder already is the
 * band.
 */
export function buildStageBandNodes(
  stages: Stage[],
  collapsedStageIndexes: ReadonlySet<number>,
  onCollapse: (stageIndex: number) => void,
): Node<BuilderStageBandData>[] {
  return stages
    .filter(stage => !collapsedStageIndexes.has(stage.index) && stage.nodeIds.length > 0)
    .map(stage => {
      const width = stage.xEnd - stage.xStart + BAND_PADDING * 2;
      const height = stage.yEnd - stage.yStart + BAND_PADDING * 2 + BAND_LABEL_HEADROOM;
      return {
        id: bandId(stage.index),
        type: BUILDER_STAGE_BAND_TYPE,
        position: {
          x: stage.xStart - BAND_PADDING,
          y: stage.yStart - BAND_PADDING - BAND_LABEL_HEADROOM,
        },
        width,
        height,
        data: {
          stageIndex: stage.index,
          label: stage.label,
          width,
          height,
          stepCount: stage.nodeIds.length,
          onCollapse,
        },
        selectable: false,
        draggable: false,
        deletable: false,
        focusable: false,
        zIndex: -1,
      };
    });
}

/**
 * Replaces each collapsed stage's members with one placeholder over that
 * stage's bounding box, and reroutes every edge that touched a hidden member
 * to the placeholder so connectivity across the collapse stays visible.
 * Untouched stages pass through as-is.
 */
export function applyStageCollapse(
  nodes: Node<WorkflowNodeData>[],
  edges: Edge<WorkflowEdgeData>[],
  stages: Stage[],
  collapsedStageIndexes: ReadonlySet<number>,
  onExpand: (stageIndex: number) => void,
): { nodes: Node<WorkflowNodeData | BuilderStagePlaceholderData>[]; edges: Edge<WorkflowEdgeData>[] } {
  if (collapsedStageIndexes.size === 0) return { nodes, edges };

  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const stageForNode = new Map<string, number>();
  for (const stage of stages) {
    for (const id of stage.nodeIds) stageForNode.set(id, stage.index);
  }

  const collapsedStages = stages.filter(stage => collapsedStageIndexes.has(stage.index));
  const hiddenIds = new Set(collapsedStages.flatMap(stage => stage.nodeIds));

  const placeholders = collapsedStages.map(stage => {
    const members = stage.nodeIds
      .map(id => nodeById.get(id))
      .filter((node): node is Node<WorkflowNodeData> => Boolean(node));
    const data: BuilderStagePlaceholderData = {
      stageIndex: stage.index,
      label: stage.label,
      nodeIds: stage.nodeIds,
      stepLabels: members.map(
        node => node.data.experience?.display_name?.trim() || node.data.nodeId,
      ),
      // A hidden preflight failure is the one thing a collapse must not swallow.
      hasIssue: members.some(node => node.data.hasIssue),
      onExpand,
    };
    return {
      id: placeholderId(stage.index),
      type: BUILDER_STAGE_PLACEHOLDER_TYPE,
      position: {
        x: (stage.xStart + stage.xEnd) / 2 - PLACEHOLDER_WIDTH / 2,
        y: (stage.yStart + stage.yEnd) / 2 - PLACEHOLDER_HEIGHT / 2,
      },
      width: PLACEHOLDER_WIDTH,
      height: PLACEHOLDER_HEIGHT,
      data,
      selectable: false,
      draggable: false,
      deletable: false,
    };
  });

  const resolveEndpoint = (id: string): string => {
    if (!hiddenIds.has(id)) return id;
    const stageIndex = stageForNode.get(id);
    return stageIndex != null ? placeholderId(stageIndex) : id;
  };

  const seen = new Set<string>();
  const remapped: Edge<WorkflowEdgeData>[] = [];
  for (const edge of edges) {
    const source = resolveEndpoint(edge.source);
    const target = resolveEndpoint(edge.target);
    if (source === target) continue; // wholly inside one collapsed stage
    const key = `${source}->${target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    remapped.push(
      source === edge.source && target === edge.target
        ? edge
        : { ...edge, id: `collapsed:${key}`, source, target, label: undefined },
    );
  }

  return {
    nodes: [...nodes.filter(node => !hiddenIds.has(node.id)), ...placeholders],
    edges: remapped,
  };
}

/** Stage indexes worth collapsing in one action: those holding more than one step. */
export function collapsibleStageIndexes(stages: Stage[]): number[] {
  return stages.filter(stage => stage.nodeIds.length > 1).map(stage => stage.index);
}
