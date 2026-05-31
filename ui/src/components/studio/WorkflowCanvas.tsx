import { useCallback, useEffect } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node as FlowNode,
  type Edge as FlowEdge,
} from "reactflow";
import * as dagre from "dagre";
import "reactflow/dist/style.css";

const getLayoutedElements = (nodes: FlowNode[], edges: FlowEdge[]) => {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", ranksep: 80, nodesep: 40, marginx: 32, marginy: 32 });

  nodes.forEach((n) => g.setNode(n.id, { width: 180, height: 60 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  return {
    nodes: nodes.map((n) => {
      const { x, y } = g.node(n.id);
      return { ...n, position: { x: x - 90, y: y - 30 } };
    }),
    edges,
  };
};

interface Props {
  workflowNodes: Array<{ id: string; type: string; config: Record<string, unknown> }>;
  workflowEdges: Array<{ from: string; to: string | string[] }>;
}

export function WorkflowCanvas({ workflowNodes, workflowEdges }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode[]>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge[]>([]);

  useEffect(() => {
    const rawNodes: FlowNode[] = workflowNodes.map((n) => ({
      id: n.id,
      data: { label: n.id, nodeType: n.type },
      position: { x: 0, y: 0 },  // dagre overrides this
    }));

    const rawEdges: FlowEdge[] = workflowEdges.map((e, i) => ({
      id: `e-${i}`,
      source: e.from,
      target: Array.isArray(e.to) ? e.to[0] : e.to,
      animated: true,
    }));

    const { nodes: ln, edges: le } = getLayoutedElements(rawNodes, rawEdges);
    setNodes(ln);
    setEdges(le);
  }, [workflowNodes, workflowEdges, setNodes, setEdges]);

  return (
    <div style={{ width: "100%", height: 420, background: "#fff", borderRadius: 10, border: "1px solid rgba(13,27,42,0.09)" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        defaultViewport={{ x: 0, y: 0, zoom: 0.85 }}
      >
        <Background gap={16} size={1} color="rgba(13,27,42,0.05)" />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  );
}