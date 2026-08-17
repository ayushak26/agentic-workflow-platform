import type { FieldSpec, NodeTypeManifest } from '../../../api/types';
import { DefaultsEditor } from './ConfigureTab';
import { SchemaBuilder } from './SchemaBuilder';

/**
 * WorkflowInputAgent's config editor.
 *
 * `fields` is declared as `FieldSpec` rows — the same shape vocabulary the
 * structured-output builder uses, `List<Enum>` included — plus two things
 * that only make sense for a value entering the workflow: where to read it
 * from (`source`), and this node's `sample` fallback for Test/Simulate.
 * Reusing `SchemaBuilder` rather than a parallel editor is what keeps
 * "Ask AI to draft this", the enum/list authoring UI, and the inline
 * "add at least one allowed value" validation identical to Structured
 * Output instead of a second, drifting implementation.
 */

type InputBinding = FieldSpec & { source?: string | null };

export function WorkflowInputAgentConfig({
  config,
  onChange,
  presets,
}: {
  config: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  presets: NodeTypeManifest['presets'];
}) {
  const source = (config.source as string | undefined) ?? 'manual';
  const fields = (config.fields as InputBinding[] | undefined) ?? [];
  const sample = (config.sample as Record<string, unknown> | undefined) ?? {};

  return (
    <div>
      {(presets ?? []).length > 0 && (
        <label className="block text-[11px] font-medium text-ink-700">
          Where this data comes from
          <select
            className="builder-field mt-1"
            onChange={event => onChange({ ...config, source: event.target.value })}
            value={source}
          >
            {(presets ?? []).map(preset => (
              <option key={preset.id} value={preset.id}>{preset.label}</option>
            ))}
          </select>
        </label>
      )}

      <SchemaBuilder
        fields={fields as unknown as FieldSpec[]}
        helperText="What enters the workflow — every downstream step addresses these by name. Use “list, holds: enum” for a field that can carry more than one of a fixed set of values."
        onChange={next => onChange({ ...config, fields: next as InputBinding[] })}
        title="Incoming inputs"
        topLevelExtra={(field, onFieldChange) => {
          const binding = field as InputBinding;
          return (
            <label className="block text-[11px] font-medium text-ink-700">
              Comes from
              <input
                className="builder-field mt-1 font-mono"
                onChange={event => onFieldChange(
                  { ...field, source: event.target.value || null } as FieldSpec,
                )}
                placeholder={`inputs.${field.name || 'field_name'}`}
                value={binding.source ?? ''}
              />
              <span className="mt-0.5 block text-[10px] font-normal text-ink-500">
                Leave blank to read <span className="font-mono">inputs.{field.name || '…'}</span> —
                what a caller supplies under this name. Point it at{' '}
                <span className="font-mono">outputs.&lt;node&gt;.data.&lt;field&gt;</span> to
                take this value from an earlier step instead.
              </span>
            </label>
          );
        }}
      />

      <section className="mt-4">
        <div className="builder-panel-heading">Sample values</div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          Used by Test and Simulate so this workflow can run before its real
          source is connected. A real value always wins.
        </p>
        <DefaultsEditor
          defaults={sample}
          onChange={next => onChange({ ...config, sample: next })}
        />
      </section>
    </div>
  );
}
