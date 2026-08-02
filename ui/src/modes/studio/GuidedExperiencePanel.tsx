import { useMemo, useState } from 'react';
import type { Node } from 'reactflow';

import { humanizeIdentifier } from './guided/runtime-model';
import type { NodeExperienceSpec, WorkflowNodeData, YamlWorkflow } from './yaml-bridge';

const DEFAULT_STAGE_OPTIONS = [
  ['prepare', 'Prepare'],
  ['understand', 'Understand'],
  ['gather', 'Gather'],
  ['create', 'Create'],
  ['check', 'Check'],
  ['finalise', 'Finalise'],
] as const;

type PreviewState = 'working' | 'waiting' | 'completed' | 'failed';

function cleanExperience(value: NodeExperienceSpec): NodeExperienceSpec | undefined {
  const cleaned = Object.fromEntries(Object.entries(value).filter(([, item]) => (
    item !== '' && item !== undefined && item !== false && (!Array.isArray(item) || item.length > 0)
  ))) as NodeExperienceSpec;
  return Object.keys(cleaned).length > 0 ? cleaned : undefined;
}

export function GuidedExperiencePanel({
  selected,
  workflow,
  onChange,
}: {
  selected: Node<WorkflowNodeData> | null;
  workflow: YamlWorkflow;
  onChange: (experience: NodeExperienceSpec | undefined) => void;
}) {
  const [previewState, setPreviewState] = useState<PreviewState>('working');
  const experience = selected?.data.experience ?? {};
  const fallbackName = selected ? humanizeIdentifier(selected.data.nodeId) : 'Workflow step';
  const stageOptions = useMemo(() => {
    const custom = (workflow.experience?.stages ?? []).map(stage => [stage.id, stage.display_name] as const);
    return [...custom, ...DEFAULT_STAGE_OPTIONS].filter((item, index, values) => (
      values.findIndex(candidate => candidate[0] === item[0]) === index
    ));
  }, [workflow.experience?.stages]);

  if (!selected) {
    return (
      <div className="builder-panel-empty">
        <strong>Select a node to design its Guided Run card.</strong>
        <span>Business-language labels and explanations stay separate from the technical node ID.</span>
      </div>
    );
  }

  function update<K extends keyof NodeExperienceSpec>(key: K, value: NodeExperienceSpec[K]) {
    onChange(cleanExperience({ ...experience, [key]: value }));
  }

  const displayName = experience.display_name || fallbackName;
  const purpose = experience.purpose || 'Explain why this activity is needed for the final result.';
  const contribution = experience.contribution || 'Describe what later work receives from this activity.';
  const expectedOutput = experience.expected_output || 'Describe the usable result this activity produces.';
  const readabilityIssues = [
    !experience.display_name || /[_/]|(agent|node|\bllm\b|\bapi\b)/i.test(experience.display_name)
      ? 'Use a short business-step name without technical terms.' : null,
    !experience.purpose ? 'Explain why this step exists.' : null,
    !experience.contribution ? 'Explain how this result helps later work.' : null,
    !experience.expected_output ? 'Name the result a user can expect.' : null,
    !experience.failure_message ? 'Add a calm, practical failure explanation.' : null,
  ].filter(Boolean) as string[];

  const previewCopy: Record<PreviewState, { eyebrow: string; headline: string; detail: string }> = {
    working: { eyebrow: 'Current work', headline: `Working on ${displayName.toLowerCase()}`, detail: purpose },
    waiting: { eyebrow: 'Waiting for you', headline: `${displayName} needs review`, detail: contribution },
    completed: { eyebrow: 'Contribution ready', headline: `${displayName} completed`, detail: expectedOutput },
    failed: { eyebrow: 'Needs attention', headline: `${displayName} could not finish`, detail: experience.failure_message || 'Completed work remains safe. Review the input or retry from the last checkpoint.' },
  };

  return (
    <div className="guided-experience-panel">
      <div className="builder-inspector-section">
        <div className="builder-section-title">Guided Run identity</div>
        <label className="guided-builder-field">
          <span>Business step name <strong>Required to publish as visible</strong></span>
          <input
            value={experience.display_name ?? ''}
            onChange={event => update('display_name', event.target.value)}
            placeholder={fallbackName}
          />
          <small>Shown instead of <code>{selected.data.nodeId}</code>.</small>
        </label>
        <label className="guided-builder-field">
          <span>Business stage</span>
          <select value={experience.stage_id ?? ''} onChange={event => update('stage_id', event.target.value || undefined)}>
            <option value="">Infer from this workflow</option>
            {stageOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <label className="guided-builder-field">
          <span>Visibility</span>
          <select value={experience.visibility ?? 'standard'} onChange={event => update('visibility', event.target.value as NodeExperienceSpec['visibility'])}>
            <option value="standard">Always visible</option>
            <option value="summary">Summary only</option>
            <option value="advanced">Advanced details only</option>
          </select>
        </label>
      </div>

      <div className="builder-inspector-section">
        <div className="builder-section-title">Explain the work</div>
        <label className="guided-builder-field">
          <span>Why this step exists</span>
          <textarea rows={3} value={experience.purpose ?? ''} onChange={event => update('purpose', event.target.value)} placeholder="Why is this work needed for the final result?" />
        </label>
        <label className="guided-builder-field">
          <span>Contribution to later work</span>
          <textarea rows={3} value={experience.contribution ?? ''} onChange={event => update('contribution', event.target.value)} placeholder="What will later stages use from this result?" />
        </label>
        <label className="guided-builder-field">
          <span>Expected output</span>
          <input value={experience.expected_output ?? ''} onChange={event => update('expected_output', event.target.value)} placeholder="A structured requirement checklist" />
        </label>
        <label className="guided-builder-field">
          <span>Receiving steps</span>
          <input
            value={(experience.receiving_steps ?? []).join(', ')}
            onChange={event => update('receiving_steps', event.target.value.split(',').map(item => item.trim()).filter(Boolean))}
            placeholder="Evidence mapping, Proposal planning"
          />
          <small>Comma-separated business names; these describe the handoff.</small>
        </label>
      </div>

      <div className="builder-inspector-section">
        <div className="builder-section-title">Failure and responsibility</div>
        <label className="guided-builder-field">
          <span>Practical failure explanation</span>
          <textarea rows={3} value={experience.failure_message ?? ''} onChange={event => update('failure_message', event.target.value)} placeholder="What could not finish, what remains safe, and what should the user do?" />
        </label>
        <label className="guided-builder-field">
          <span>Agent role <em>Optional</em></span>
          <input value={experience.agent_role ?? ''} onChange={event => update('agent_role', event.target.value)} placeholder="Independent Reviewer" />
        </label>
        <label className="guided-role-toggle">
          <input type="checkbox" checked={Boolean(experience.show_agent_role)} onChange={event => update('show_agent_role', event.target.checked)} />
          <span>Show the role because it clarifies responsibility or independent review</span>
        </label>
      </div>

      <div className="builder-inspector-section">
        <div className="builder-section-title">Runtime card preview</div>
        <div className="guided-preview-tabs" role="tablist" aria-label="Preview state">
          {(['working', 'waiting', 'completed', 'failed'] as PreviewState[]).map(state => (
            <button key={state} type="button" className={previewState === state ? 'is-active' : ''} onClick={() => setPreviewState(state)}>{state}</button>
          ))}
        </div>
        <div className={`guided-runtime-preview is-${previewState}`}>
          <span>{previewCopy[previewState].eyebrow}</span>
          <strong>{previewCopy[previewState].headline}</strong>
          <p>{previewCopy[previewState].detail}</p>
          <small>Expected handoff: {contribution}</small>
        </div>
      </div>

      <div className="builder-inspector-section">
        <div className="builder-section-title">Non-technical readability check</div>
        {readabilityIssues.length === 0 ? (
          <div className="guided-readability-ready"><span aria-hidden="true">✓</span> Ready for Guided Run</div>
        ) : (
          <ul className="guided-readability-list">
            {readabilityIssues.map(issue => <li key={issue}>{issue}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}
