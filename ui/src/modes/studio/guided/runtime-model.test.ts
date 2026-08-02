import { describe, expect, it } from 'vitest';
import type { RunDetail } from '../../../api/types';
import type { NodeStatus } from '../cockpit-state';
import type { YamlWorkflow } from '../yaml-bridge';
import {
  buildGuidedRuntimeModel,
  collectGuidedArtifacts,
  humanizeIdentifier,
  nodeStatusesFromRun,
} from './runtime-model';

describe('humanizeIdentifier', () => {
  it('splits snake_case and strips agent/node words', () => {
    expect(humanizeIdentifier('map_requirements_agent')).toBe('Map requirements');
  });

  it('splits camelCase', () => {
    expect(humanizeIdentifier('searchForSignals')).toBe('Search For Signals');
  });

  it('falls back to a generic label for an empty/all-stripped id', () => {
    expect(humanizeIdentifier('')).toBe('Workflow step');
    expect(humanizeIdentifier('agent_node')).toBe('Workflow step');
  });
});

function workflow(nodes: YamlWorkflow['nodes'], experience?: YamlWorkflow['experience']): YamlWorkflow {
  return { name: 'Test workflow', version: '1.0', nodes, edges: [], experience };
}

describe('buildGuidedRuntimeModel', () => {
  it('infers a stage from node id/type when no experience is authored', () => {
    const model = buildGuidedRuntimeModel({
      workflow: workflow([
        { id: 'search_for_signals', type: 'WebSearchAgent' },
        { id: 'render_proposal', type: 'PDFProposalRenderer' },
      ]),
      nodeStatuses: {},
      outputs: {},
    });
    const byId = new Map(model.steps.map(step => [step.id, step]));
    expect(byId.get('search_for_signals')!.stageId).toBe('gather');
    expect(byId.get('render_proposal')!.stageId).toBe('finalise');
  });

  it('an explicit node experience.stage_id wins over inference', () => {
    const model = buildGuidedRuntimeModel({
      workflow: workflow([
        {
          id: 'search_for_signals',
          type: 'WebSearchAgent',
          experience: { stage_id: 'create' },
        },
      ]),
      nodeStatuses: {},
      outputs: {},
    });
    expect(model.steps[0].stageId).toBe('create');
  });

  it('stage state: a failed member marks the stage attention even if others are done', () => {
    const model = buildGuidedRuntimeModel({
      workflow: workflow([
        { id: 'a', type: 'Literal', experience: { stage_id: 'create' } },
        { id: 'b', type: 'Literal', experience: { stage_id: 'create' } },
      ]),
      nodeStatuses: { a: 'done', b: 'failed' },
      outputs: {},
    });
    const createStage = model.stages.find(stage => stage.id === 'create')!;
    expect(createStage.state).toBe('attention');
  });

  it('stage state: all-terminal-and-done nodes mark the stage completed', () => {
    const model = buildGuidedRuntimeModel({
      workflow: workflow([
        { id: 'a', type: 'Literal', experience: { stage_id: 'create' } },
      ]),
      nodeStatuses: { a: 'done' },
      outputs: {},
    });
    const createStage = model.stages.find(stage => stage.id === 'create')!;
    expect(createStage.state).toBe('completed');
    expect(createStage.completedCount).toBe(1);
  });

  it('current step prefers the gated node over the active node', () => {
    const model = buildGuidedRuntimeModel({
      workflow: workflow([
        { id: 'a', type: 'Literal', experience: { stage_id: 'create' } },
        { id: 'b', type: 'Literal', experience: { stage_id: 'create' } },
      ]),
      nodeStatuses: { a: 'active', b: 'paused' },
      outputs: {},
      activeNodeId: 'a',
      gateNodeId: 'b',
    });
    expect(model.currentStep?.id).toBe('b');
  });

  it('advanced-visibility nodes are excluded from current-step/contribution selection', () => {
    const model = buildGuidedRuntimeModel({
      workflow: workflow([
        {
          id: 'hidden',
          type: 'Literal',
          experience: { stage_id: 'create', visibility: 'advanced' },
        },
      ]),
      nodeStatuses: { hidden: 'done' },
      outputs: {},
    });
    expect(model.contributions).toHaveLength(0);
    expect(model.currentStep).toBeNull();
  });

  it('uses the workflow goal, or description, or a humanized name as a fallback', () => {
    const withGoal = buildGuidedRuntimeModel({
      workflow: workflow([{ id: 'a', type: 'Literal' }], { goal: 'Ship the thing.' }),
      nodeStatuses: {},
      outputs: {},
    });
    expect(withGoal.goal).toBe('Ship the thing.');

    const withoutGoal = buildGuidedRuntimeModel({
      workflow: { ...workflow([{ id: 'a', type: 'Literal' }]), description: 'A helpful description.' },
      nodeStatuses: {},
      outputs: {},
    });
    expect(withoutGoal.goal).toBe('A helpful description.');
  });
});

describe('collectGuidedArtifacts', () => {
  it('collects deduplicated file-key fields with a recognised extension', () => {
    const artifacts = collectGuidedArtifacts({
      render: { pdf_key: 'workflows/x/out.pdf', minio_key: 'workflows/x/out.pdf' },
      other: { docx_key: 'workflows/x/out.docx', notes: 'not a key' },
      ignored: { some_key: 'workflows/x/out.exe' },
    });
    expect(artifacts.map(a => a.key).sort()).toEqual([
      'workflows/x/out.docx',
      'workflows/x/out.pdf',
    ]);
  });

  it('returns nothing for outputs with no recognisable file references', () => {
    expect(collectGuidedArtifacts({ node: { text: 'hello' } })).toEqual([]);
  });
});

describe('nodeStatusesFromRun', () => {
  const wf = workflow([{ id: 'a', type: 'Literal' }, { id: 'b', type: 'Literal' }]);

  it('prefers the live run-detail node status over everything else', () => {
    const liveRun = {
      node_runs: { a: { status: 'running' } },
      outputs: { a: {} },
    } as unknown as RunDetail;
    const statuses = nodeStatusesFromRun(wf, { a: 'done' as NodeStatus }, liveRun, false);
    expect(statuses.a).toBe('active');
  });

  it('falls back to the SSE-derived status, then output presence, then pending/skipped', () => {
    const liveRun = { node_runs: {}, outputs: { b: {} } } as unknown as RunDetail;
    const statuses = nodeStatusesFromRun(wf, { a: 'active' as NodeStatus }, liveRun, false);
    expect(statuses.a).toBe('active');
    expect(statuses.b).toBe('done');
  });

  it('marks an untouched node skipped once the run is finished, pending otherwise', () => {
    const notFinished = nodeStatusesFromRun(wf, {}, null, false);
    expect(notFinished.a).toBe('pending');
    const finished = nodeStatusesFromRun(wf, {}, null, true);
    expect(finished.a).toBe('skipped');
  });
});
