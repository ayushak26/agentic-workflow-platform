import { describe, expect, it } from 'vitest';
import type { Node } from 'reactflow';
import { matchNodes } from './node-search';
import type { WorkflowNodeData } from '../yaml-bridge';

function node(id: string, data: Partial<WorkflowNodeData> = {}): Node<WorkflowNodeData> {
  return {
    id,
    type: 'workflow',
    position: { x: 0, y: 0 },
    data: { nodeId: id, typeName: 'AITaskAgent', config: {}, ...data },
  };
}

const nodes = [
  node('classify_request', { experience: { display_name: 'Understand Customer Request' } }),
  node('notify_customer', { typeName: 'EmailAgent' }),
  node('update_case', {
    typeName: 'MCPToolAgent',
    config: { server_id: 'dynamics_crm', tool: 'update_case' },
  }),
  node('customer_summary', { hasIssue: true }),
];

describe('matchNodes', () => {
  it('lists every step when nothing has been typed', () => {
    expect(matchNodes(nodes, '').map(match => match.id)).toEqual([
      'classify_request',
      'notify_customer',
      'update_case',
      'customer_summary',
    ]);
  });

  it('finds a step by its business name or its id', () => {
    expect(matchNodes(nodes, 'understand').map(match => match.id)).toEqual(['classify_request']);
    expect(matchNodes(nodes, 'notify_c').map(match => match.id)).toEqual(['notify_customer']);
  });

  it('finds an MCP step by the system or tool it reaches', () => {
    expect(matchNodes(nodes, 'dynamics').map(match => match.id)).toEqual(['update_case']);
    expect(matchNodes(nodes, 'email').map(match => match.id)).toEqual(['notify_customer']);
  });

  it('ranks a step whose own name starts with the query first', () => {
    // "customer" appears in all three of these; only one starts with it.
    expect(matchNodes(nodes, 'customer').map(match => match.id)).toEqual([
      'customer_summary',
      'notify_customer',
      'classify_request',
    ]);
  });

  it('shows the business name as the label and keeps the id in the detail line', () => {
    const [match] = matchNodes(nodes, 'understand');
    expect(match.label).toBe('Understand Customer Request');
    expect(match.detail).toBe('AI Task · classify_request');
  });

  it('carries the preflight-issue flag through so search can show it', () => {
    expect(matchNodes(nodes, 'customer_summary')[0].hasIssue).toBe(true);
  });

  it('caps how many results it returns', () => {
    const many = Array.from({ length: 40 }, (_, index) => node(`step_${index}`));
    expect(matchNodes(many, 'step')).toHaveLength(12);
    expect(matchNodes(many, 'step', 3)).toHaveLength(3);
  });

  it('returns nothing for a query that matches no step', () => {
    expect(matchNodes(nodes, 'zzz')).toEqual([]);
  });
});
