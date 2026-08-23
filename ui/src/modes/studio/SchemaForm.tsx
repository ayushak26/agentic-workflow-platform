/* JSON Schema is recursive and permits arbitrary extension keywords. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, type ChangeEvent } from 'react';
import { AskAiDialog } from './builder/AskAiDialog';
import { ResourceSelect } from './builder/ResourceSelect';
import { PromptDraftAssistant } from './PromptDraftAssistant';

// Treat these field names as multiline regardless of schema (the schema
// doesn't carry "format: textarea" by default from Pydantic).
const MULTILINE_NAMES = new Set([
  'prompt_template', 'system_prompt', 'prompt', 'generation_prompt',
  'objective', 'description', 'instructions', 'context',
]);

// Subset of MULTILINE_NAMES that's actually LLM prompt text (as opposed to
// a plain description/context string) — these get the "Draft Prompt"
// affordance since PromptDraftAssistant is specifically for prompt authoring.
const PROMPT_FIELD_NAMES = new Set([
  'prompt_template', 'system_prompt', 'prompt', 'generation_prompt', 'instructions',
]);

type Schema = any;

export function SchemaForm({
  schema,
  value,
  onChange,
  hiddenFields = [],
  typeName,
}: {
  schema: Schema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  hiddenFields?: string[];
  // Which node type this config belongs to — only needed to scope the
  // "Draft Prompt" assistant; omit it and that affordance just won't show.
  typeName?: string;
}) {
  if (!schema || schema.type !== 'object' || !schema.properties) {
    return <div className="text-sm text-ink-500">No editable fields.</div>;
  }

  const required: string[] = schema.required ?? [];

  return (
    <div className="space-y-4">
      {Object.entries(schema.properties as Record<string, Schema>)
        .filter(([name]) => !hiddenFields.includes(name))
        .map(([name, propSchema]) => (
        <FieldRenderer
          key={name}
          name={name}
          schema={propSchema}
          required={required.includes(name)}
          value={value[name]}
          typeName={typeName}
          onChange={v => {
            if (v === undefined) {
              const next = { ...value };
              delete next[name];
              onChange(next);
            } else {
              onChange({ ...value, [name]: v });
            }
          }}
        />
      ))}
    </div>
  );
}

function FieldRenderer({
  name,
  schema,
  required,
  value,
  onChange,
  typeName,
}: {
  name: string;
  schema: Schema;
  required: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
  typeName?: string;
}) {
  const [drafting, setDrafting] = useState(false);
  const [askingAi, setAskingAi] = useState(false);
  const label = (
    <div className="flex items-center justify-between gap-2">
      <label className="block text-xs font-medium text-ink-700">
        {name}
        {required && <span className="text-bad ml-1">*</span>}
      </label>
      {typeName && (
        <button
          className="flex-none text-[10px] font-medium text-accent-700 hover:underline"
          onClick={() => setAskingAi(true)}
          title={`Ask AI about the "${name}" field`}
          type="button"
        >
          Ask AI
        </button>
      )}
    </div>
  );
  const askAiDialog = typeName && askingAi ? (
    <AskAiDialog
      context={{ node_type: typeName, field: name }}
      onClose={() => setAskingAi(false)}
      starterQuestion={`Explain the "${name}" configuration field on ${typeName} — what it controls and how I should set it.`}
      title={`Ask AI — ${typeName}.${name}`}
    />
  ) : null;

  // Resolve anyOf with null → optional field of the non-null branch
  const effective = resolveOptional(schema);
  const isOptional = effective !== schema;

  // Knowledge Studio-backed resources (x-resource marker from the backend
  // config schema) → live registry select, not a raw identifier input.
  const resourceKind = effective['x-resource'];
  if (resourceKind === 'collection' || resourceKind === 'retrieval_profile') {
    return (
      <div>
        {label}
        <ResourceSelect
          resource={resourceKind}
          value={typeof value === 'string' ? value : ''}
          onChange={onChange}
        />
        {effective.description && <Hint text={effective.description} />}
        {askAiDialog}
      </div>
    );
  }

  // Enum → select
  if (effective.enum && Array.isArray(effective.enum)) {
    return (
      <div>
        {label}
        <select
          value={String(value ?? effective.default ?? '')}
          onChange={e => onChange(e.target.value)}
          className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border"
        >
          {isOptional && <option value="">(none)</option>}
          {effective.enum.map((opt: string) => (
            <option key={opt} value={opt}>
              {effective['x-enum-labels']?.[opt] ?? opt}
            </option>
          ))}
        </select>
        {effective.description && <Hint text={effective.description} />}
        {askAiDialog}
      </div>
    );
  }

  // Boolean → checkbox
  if (effective.type === 'boolean') {
    return (
      <div>
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-start gap-2">
            <input
              id={`f-${name}`}
              type="checkbox"
              checked={Boolean(value)}
              onChange={e => onChange(e.target.checked)}
              className="mt-0.5"
            />
            <label htmlFor={`f-${name}`} className="text-sm">{name}</label>
          </span>
          {typeName && (
            <button
              className="flex-none text-[10px] font-medium text-accent-700 hover:underline"
              onClick={() => setAskingAi(true)}
              title={`Ask AI about the "${name}" field`}
              type="button"
            >
              Ask AI
            </button>
          )}
        </div>
        {effective.description && <Hint text={effective.description} />}
        {askAiDialog}
      </div>
    );
  }

  // Number / integer → number input
  if (effective.type === 'integer' || effective.type === 'number') {
    return (
      <div>
        {label}
        <input
          type="number"
          step={effective.type === 'integer' ? 1 : 'any'}
          value={value === undefined || value === null ? '' : String(value)}
          onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))}
          className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border"
        />
        {askAiDialog}
      </div>
    );
  }

  // String → text input or textarea
  if (effective.type === 'string') {
    const multiline = MULTILINE_NAMES.has(name);
    const isPromptField = typeName && PROMPT_FIELD_NAMES.has(name);
    const common = {
      value: typeof value === 'string' ? value : '',
      onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(e.target.value),
      className: 'mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border',
    };
    return (
      <div>
        <div className="flex items-center justify-between">
          {label}
          {isPromptField && (
            <button
              type="button"
              onClick={() => setDrafting(true)}
              className="text-xs text-accent-700 hover:underline"
            >
              ✨ Draft Prompt
            </button>
          )}
        </div>
        {multiline ? (
          <textarea {...common} rows={6} />
        ) : (
          <input type="text" {...common} />
        )}
        {effective.description && <Hint text={effective.description} />}
        {isPromptField && drafting && typeName && (
          <PromptDraftAssistant
            typeName={typeName}
            fieldName={name}
            onInsert={onChange}
            onClose={() => setDrafting(false)}
          />
        )}
        {askAiDialog}
      </div>
    );
  }

  // Array of strings → newline-separated textarea
  if (effective.type === 'array' && (effective.items?.type === 'string')) {
    const arr = Array.isArray(value) ? value as string[] : [];
    return (
      <div>
        {label}
        <textarea
          rows={3}
          value={arr.join('\n')}
          onChange={e => onChange(e.target.value.split('\n').filter(Boolean))}
          className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border font-mono"
        />
        <Hint text="One value per line." />
        {askAiDialog}
      </div>
    );
  }

  // Fallback: raw JSON textarea for nested objects / object-of-objects / complex arrays.
  // Captures everything we don't have a specialized renderer for, without losing edit capability.
  return (
    <div>
      {label}
      <textarea
        rows={4}
        value={value === undefined ? '' : JSON.stringify(value, null, 2)}
        onChange={e => {
          try {
            onChange(e.target.value === '' ? null : JSON.parse(e.target.value));
          } catch {
            // Don't update on invalid JSON; user keeps typing.
          }
        }}
        className="mt-1 block w-full rounded-md border-slate-300 text-xs py-1.5 px-2 border font-mono"
      />
      <Hint text="Edit as JSON. Phase 11 may add a typed editor." />
      {askAiDialog}
    </div>
  );
}

function Hint({ text }: { text: string }) {
  return <p className="text-xs text-ink-500 mt-1">{text}</p>;
}

function resolveOptional(schema: Schema): Schema {
  // Pydantic emits Optional[X] as anyOf [X, null] in JSON Schema.
  const variants = schema?.anyOf ?? schema?.oneOf;
  if (!Array.isArray(variants)) return schema;
  const nonNull = variants.find((v: any) => v.type !== 'null');
  return nonNull ?? schema;
}
