import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type { FieldKind, FieldSpec, SchemaPreview } from '../../../api/types';

/**
 * The visual structured-output builder.
 *
 * A workflow author defines what the AI must return by editing rows, not by
 * writing JSON Schema. One recursive row component covers every depth: a
 * top-level field, an object's child, and a list's item shape are the same
 * thing, which is why nesting `equipment → process → flow_rate → value/unit`
 * needs no separate editor per level.
 *
 * Every edit is compiled by the backend (`/builder/schema-preview`) using the
 * exact compiler the runtime uses, so an invalid row is reported here rather
 * than at run time — and the JSON Schema shown is the one the model will
 * actually be constrained by.
 */

const KINDS: Array<{ value: FieldKind; label: string }> = [
  { value: 'string', label: 'Text (short)' },
  { value: 'text', label: 'Text (long)' },
  { value: 'enum', label: 'One of a fixed set' },
  { value: 'number', label: 'Number' },
  { value: 'integer', label: 'Whole number' },
  { value: 'boolean', label: 'Yes / no' },
  { value: 'date', label: 'Date' },
  { value: 'object', label: 'Group of fields' },
  { value: 'list', label: 'List' },
];

const ITEM_KINDS: FieldKind[] = [
  'string', 'text', 'number', 'integer', 'boolean', 'enum', 'date', 'object',
];

export function newField(name = ''): FieldSpec {
  return { name, type: 'string', description: '', required: true, nullable: false };
}

/** Defaults a type change needs to stay valid.
 *  Switching to `enum` without values, or `list` without an item type, produces
 *  a row the compiler rejects — so the switch supplies the missing part rather
 *  than leaving the author with an error to decode. */
function coerceForType(field: FieldSpec, type: FieldKind): FieldSpec {
  const next: FieldSpec = { ...field, type };
  if (type === 'enum' && (!next.enum_values || next.enum_values.length === 0)) {
    next.enum_values = ['option_a', 'option_b'];
  }
  if (type === 'list') {
    next.item_type = next.item_type ?? 'string';
    if (next.item_type === 'object' && (next.fields ?? []).length === 0) {
      next.fields = [newField('name')];
    }
  }
  if (type === 'object' && (next.fields ?? []).length === 0) {
    next.fields = [newField('name')];
  }
  if (type !== 'object' && type !== 'list') {
    delete next.fields;
    delete next.item_type;
    delete next.item_enum_values;
  }
  if (type !== 'enum') delete next.enum_values;
  return next;
}

function FieldRow({
  field,
  depth,
  onChange,
  onRemove,
  onMove,
  isFirst,
  isLast,
}: {
  field: FieldSpec;
  depth: number;
  onChange: (next: FieldSpec) => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  const [expanded, setExpanded] = useState(depth === 0);
  const hasChildren = field.type === 'object'
    || (field.type === 'list' && field.item_type === 'object');
  const showEnum = field.type === 'enum'
    || (field.type === 'list' && field.item_type === 'enum');

  const enumValues = (field.type === 'enum' ? field.enum_values : field.item_enum_values) ?? [];
  const setEnumValues = (values: string[]) => onChange(
    field.type === 'enum'
      ? { ...field, enum_values: values }
      : { ...field, item_enum_values: values },
  );

  return (
    <div
      className="rounded-md border border-slate-200 bg-white"
      style={{ marginLeft: depth > 0 ? 12 : 0 }}
    >
      <div className="flex items-center gap-2 px-2 py-2">
        <button
          aria-label={expanded ? `Collapse ${field.name || 'field'}` : `Expand ${field.name || 'field'}`}
          className="w-4 flex-none text-ink-400 hover:text-ink-800"
          onClick={() => setExpanded(value => !value)}
          type="button"
        >
          {expanded ? '▾' : '▸'}
        </button>
        <input
          aria-label="Field name"
          className="builder-field flex-1 font-mono"
          onChange={event => onChange({ ...field, name: event.target.value })}
          placeholder="field_name"
          value={field.name}
        />
        <select
          aria-label={`Type of ${field.name || 'field'}`}
          className="builder-field w-36 flex-none"
          onChange={event => onChange(coerceForType(field, event.target.value as FieldKind))}
          value={field.type}
        >
          {KINDS.map(kind => (
            <option key={kind.value} value={kind.value}>{kind.label}</option>
          ))}
        </select>
        <label className="flex flex-none items-center gap-1 text-[11px] text-ink-600">
          <input
            checked={field.required !== false}
            onChange={event => onChange({ ...field, required: event.target.checked })}
            type="checkbox"
          />
          Required
        </label>
        <div className="flex flex-none items-center gap-0.5">
          <button
            aria-label={`Move ${field.name || 'field'} up`}
            className="px-1 text-ink-400 hover:text-ink-800 disabled:opacity-30"
            disabled={isFirst}
            onClick={() => onMove(-1)}
            type="button"
          >↑</button>
          <button
            aria-label={`Move ${field.name || 'field'} down`}
            className="px-1 text-ink-400 hover:text-ink-800 disabled:opacity-30"
            disabled={isLast}
            onClick={() => onMove(1)}
            type="button"
          >↓</button>
          <button
            aria-label={`Remove ${field.name || 'field'}`}
            className="px-1 text-ink-400 hover:text-red-600"
            onClick={onRemove}
            type="button"
          >×</button>
        </div>
      </div>

      {expanded && (
        <div className="space-y-2 border-t border-slate-100 px-3 py-2">
          <label className="block text-[11px] font-medium text-ink-700">
            What this field means
            <textarea
              className="builder-field mt-1"
              onChange={event => onChange({ ...field, description: event.target.value })}
              placeholder="Written as an instruction to whoever fills it in — e.g. 'Model designation exactly as written; never normalise it.'"
              rows={2}
              value={field.description ?? ''}
            />
          </label>

          <label className="flex items-start gap-2 text-[11px] text-ink-700">
            <input
              checked={Boolean(field.nullable)}
              className="mt-0.5"
              onChange={event => onChange({ ...field, nullable: event.target.checked })}
              type="checkbox"
            />
            <span>
              Can be null
              <span className="block text-[10px] text-ink-500">
                Tells the model to return null when the content does not state
                this, instead of inventing a plausible value.
              </span>
            </span>
          </label>

          {field.type === 'list' && (
            <label className="block text-[11px] font-medium text-ink-700">
              This list holds
              <select
                className="builder-field mt-1"
                onChange={event => onChange(
                  coerceForType(
                    { ...field, item_type: event.target.value as FieldKind },
                    'list',
                  ),
                )}
                value={field.item_type ?? 'string'}
              >
                {ITEM_KINDS.map(kind => (
                  <option key={kind} value={kind}>{kind}</option>
                ))}
              </select>
            </label>
          )}

          {showEnum && (
            <EnumEditor values={enumValues} onChange={setEnumValues} />
          )}

          {(field.type === 'number' || field.type === 'integer') && (
            <div className="flex gap-2">
              <label className="flex-1 text-[11px] font-medium text-ink-700">
                Minimum
                <input
                  className="builder-field mt-1"
                  onChange={event => onChange({
                    ...field,
                    minimum: event.target.value === '' ? null : Number(event.target.value),
                  })}
                  type="number"
                  value={field.minimum ?? ''}
                />
              </label>
              <label className="flex-1 text-[11px] font-medium text-ink-700">
                Maximum
                <input
                  className="builder-field mt-1"
                  onChange={event => onChange({
                    ...field,
                    maximum: event.target.value === '' ? null : Number(event.target.value),
                  })}
                  type="number"
                  value={field.maximum ?? ''}
                />
              </label>
            </div>
          )}

          {hasChildren && (
            <div className="mt-2">
              <div className="mb-1 text-[11px] font-semibold text-ink-700">
                {field.type === 'list' ? 'Shape of each item' : 'Fields in this group'}
              </div>
              <FieldList
                depth={depth + 1}
                fields={field.fields ?? []}
                onChange={fields => onChange({ ...field, fields })}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EnumEditor({
  values,
  onChange,
}: {
  values: string[];
  onChange: (values: string[]) => void;
}) {
  return (
    <div>
      <div className="text-[11px] font-medium text-ink-700">Allowed values</div>
      <p className="text-[10px] text-ink-500">
        The model can only return one of these. Include a catch-all such as
        &ldquo;other&rdquo; so an unusual case has somewhere to go.
      </p>
      <div className="mt-1 space-y-1">
        {values.map((value, index) => (
          <div className="flex gap-1" key={index}>
            <input
              aria-label={`Allowed value ${index + 1}`}
              className="builder-field font-mono"
              onChange={event => {
                const next = [...values];
                next[index] = event.target.value;
                onChange(next);
              }}
              value={value}
            />
            <button
              aria-label={`Remove value ${value}`}
              className="px-1 text-ink-400 hover:text-red-600"
              onClick={() => onChange(values.filter((_, position) => position !== index))}
              type="button"
            >×</button>
          </div>
        ))}
      </div>
      <button
        className="mt-1 text-[11px] font-medium text-accent-700 hover:underline"
        onClick={() => onChange([...values, ''])}
        type="button"
      >
        + Add value
      </button>
    </div>
  );
}

function FieldList({
  fields,
  depth,
  onChange,
}: {
  fields: FieldSpec[];
  depth: number;
  onChange: (fields: FieldSpec[]) => void;
}) {
  const replace = (index: number, next: FieldSpec) => {
    const copy = [...fields];
    copy[index] = next;
    onChange(copy);
  };
  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= fields.length) return;
    const copy = [...fields];
    [copy[index], copy[target]] = [copy[target], copy[index]];
    onChange(copy);
  };

  return (
    <div className="space-y-1.5">
      {fields.map((field, index) => (
        <FieldRow
          depth={depth}
          field={field}
          isFirst={index === 0}
          isLast={index === fields.length - 1}
          key={index}
          onChange={next => replace(index, next)}
          onMove={direction => move(index, direction)}
          onRemove={() => onChange(fields.filter((_, position) => position !== index))}
        />
      ))}
      <button
        className="w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => onChange([...fields, newField()])}
        type="button"
      >
        + Add field
      </button>
    </div>
  );
}

export function SchemaBuilder({
  fields,
  onChange,
  sampleContent,
}: {
  fields: FieldSpec[];
  onChange: (fields: FieldSpec[]) => void;
  /** Passed to the AI schema assistant so a suggestion is grounded in a real
   *  example of the content rather than in the description alone. */
  sampleContent?: string;
}) {
  const [preview, setPreview] = useState<SchemaPreview | null>(null);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [showSchema, setShowSchema] = useState(false);
  const [askOpen, setAskOpen] = useState(false);

  // Compiled by the backend, debounced. Doing it server-side is the point:
  // there is exactly one compiler, so what the panel says compiles is what the
  // runtime will hold the model to.
  useEffect(() => {
    if (fields.length === 0) {
      setPreview(null);
      setCompileError(null);
      return;
    }
    const timer = window.setTimeout(() => {
      api.schemaPreview(fields)
        .then(result => {
          setPreview(result);
          setCompileError(null);
        })
        .catch(error => {
          setPreview(null);
          setCompileError(error instanceof Error ? error.message : String(error));
        });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [fields]);

  const summary = useMemo(() => {
    const count = (items: FieldSpec[]): number => items.reduce(
      (total, item) => total + 1 + count(item.fields ?? []),
      0,
    );
    return count(fields);
  }, [fields]);

  return (
    <section className="mt-4">
      <div className="flex items-center justify-between">
        <div className="builder-panel-heading">Structured output</div>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setAskOpen(true)}
          type="button"
        >
          Ask AI to draft this
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        What this step must return. The platform turns these rows into the
        schema the model is constrained by — you never write JSON Schema.
      </p>

      <div className="mt-3">
        <FieldList depth={0} fields={fields} onChange={onChange} />
      </div>

      {compileError && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] text-red-800">
          <div className="font-semibold">This schema does not compile yet</div>
          <div className="mt-0.5">{compileError}</div>
        </div>
      )}

      {preview && !compileError && (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-2 text-[11px] text-emerald-900">
          <div className="flex items-center justify-between">
            <span>
              {summary} field{summary === 1 ? '' : 's'} · compiles cleanly
            </span>
            <button
              className="font-medium text-emerald-800 hover:underline"
              onClick={() => setShowSchema(value => !value)}
              type="button"
            >
              {showSchema ? 'Hide' : 'Show'} generated schema
            </button>
          </div>
          {showSchema && (
            <pre className="mt-2 max-h-64 overflow-auto rounded bg-white p-2 font-mono text-[10px] text-ink-700">
              {JSON.stringify(preview.json_schema, null, 2)}
            </pre>
          )}
        </div>
      )}

      {askOpen && (
        <SchemaAssistant
          existingFields={fields}
          onApply={next => {
            onChange(next);
            setAskOpen(false);
          }}
          onClose={() => setAskOpen(false)}
          sampleContent={sampleContent}
        />
      )}
    </section>
  );
}

/**
 * Ask AI to propose a schema.
 *
 * The proposal lands in the editor as ordinary rows the author can change or
 * reject. Nothing is applied until "Use these fields" is pressed, and once
 * applied it is deterministic configuration — no model is consulted at run time.
 */
function SchemaAssistant({
  existingFields,
  onApply,
  onClose,
  sampleContent,
}: {
  existingFields: FieldSpec[];
  onApply: (fields: FieldSpec[]) => void;
  onClose: () => void;
  sampleContent?: string;
}) {
  const [description, setDescription] = useState('');
  const [sample, setSample] = useState(sampleContent ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<FieldSpec[] | null>(null);
  const [notes, setNotes] = useState('');

  const submit = useCallback(() => {
    setBusy(true);
    setError(null);
    api.suggestSchema({
      description,
      sample_content: sample,
      existing_fields: existingFields,
    })
      .then(result => {
        if (result.status !== 'ok') {
          setError(result.message ?? 'The suggestion could not be compiled.');
          return;
        }
        setProposal(result.fields);
        setNotes(result.notes ?? '');
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  }, [description, existingFields, sample]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-ink-900">
              Describe what you want to extract
            </h3>
            <p className="mt-1 text-[11px] text-ink-500">
              The suggestion arrives as editable rows. Nothing changes until you
              accept it, and once accepted it is a fixed contract.
            </p>
          </div>
          <button
            aria-label="Close"
            className="text-ink-400 hover:text-ink-900"
            onClick={onClose}
            type="button"
          >×</button>
        </div>

        <label className="mt-4 block text-[11px] font-medium text-ink-700">
          What should this step extract?
          <textarea
            className="builder-field mt-1"
            onChange={event => setDescription(event.target.value)}
            placeholder="Customer details, product model, serial number, medium, requested flow rate, urgency, and what the customer wants."
            rows={3}
            value={description}
          />
        </label>

        <label className="mt-3 block text-[11px] font-medium text-ink-700">
          An example of the content (optional, but makes the suggestion much better)
          <textarea
            className="builder-field mt-1 font-mono"
            onChange={event => setSample(event.target.value)}
            placeholder="Paste a real customer email here."
            rows={5}
            value={sample}
          />
        </label>

        {error && (
          <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] text-red-800">
            {error}
          </div>
        )}

        {proposal && (
          <div className="mt-4 rounded-md border border-slate-200 p-3">
            <div className="text-[11px] font-semibold text-ink-800">
              Proposed fields
            </div>
            {notes && <p className="mt-1 text-[11px] text-ink-500">{notes}</p>}
            <ul className="mt-2 space-y-1 text-[11px] text-ink-700">
              {proposal.map(field => (
                <li className="font-mono" key={field.name}>
                  {field.name}
                  <span className="ml-2 text-ink-500">{field.type}</span>
                  {field.enum_values && field.enum_values.length > 0 && (
                    <span className="ml-2 text-ink-500">
                      ({field.enum_values.join(', ')})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button className="ui-button ui-button--ghost" onClick={onClose} type="button">
            Cancel
          </button>
          <button
            className="ui-button ui-button--secondary"
            disabled={busy || !description.trim()}
            onClick={submit}
            type="button"
          >
            {busy ? 'Thinking…' : proposal ? 'Try again' : 'Suggest fields'}
          </button>
          {proposal && (
            <button
              className="ui-button ui-button--primary"
              onClick={() => onApply(proposal)}
              type="button"
            >
              Use these fields
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
