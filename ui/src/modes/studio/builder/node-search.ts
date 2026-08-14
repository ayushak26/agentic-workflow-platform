import type { Node } from 'reactflow';
import { nodeTypeLabel } from '../node-presentation';
import type { WorkflowNodeData } from '../yaml-bridge';

/**
 * Matching for the Builder's jump-to-step palette.
 *
 * Searches everything a person might remember a step by — the business name it
 * shows on the canvas, its YAML id, its type, and for an MCP step the system
 * and tool it reaches — because on a long workflow "the one that writes back to
 * Dynamics" is a likelier memory than the node id someone typed months ago.
 */

export type NodeSearchMatch = {
  id: string;
  label: string;
  detail: string;
  hasIssue: boolean;
};

export function matchNodes(
  nodes: Node<WorkflowNodeData>[],
  query: string,
  limit = 12,
): NodeSearchMatch[] {
  const needle = query.trim().toLowerCase();
  const described = nodes.map(node => {
    const businessLabel = node.data.experience?.display_name?.trim();
    const typeLabel = nodeTypeLabel(node.data.typeName, node.data.config);
    return {
      id: node.id,
      label: businessLabel || node.data.nodeId,
      detail: businessLabel ? `${typeLabel} · ${node.data.nodeId}` : typeLabel,
      hasIssue: Boolean(node.data.hasIssue),
      haystack: [
        businessLabel ?? '',
        node.data.nodeId,
        node.data.typeName,
        String(node.data.config.server_id ?? ''),
        String(node.data.config.tool ?? ''),
      ].join(' ').toLowerCase(),
    };
  });

  const ranked = needle
    ? described
      .filter(item => item.haystack.includes(needle))
      // A step whose own name starts with what was typed is what the typist
      // meant; a match only in its type or connected system comes last.
      .sort((a, b) => rank(a, needle) - rank(b, needle) || a.label.localeCompare(b.label))
    : described;

  return ranked.slice(0, limit).map(({ id, label, detail, hasIssue }) => ({
    id,
    label,
    detail,
    hasIssue,
  }));
}

function rank(item: { label: string }, needle: string): number {
  const label = item.label.toLowerCase();
  if (label.startsWith(needle)) return 0;
  if (label.includes(needle)) return 1;
  return 2;
}
