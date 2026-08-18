import { useState } from 'react';

import type { FieldKind, FieldSpec } from '../../../api/types';
import { DefaultsEditor } from './ConfigureTab';
import { ModeCard } from './RouterEditor';
import { SchemaBuilder } from './SchemaBuilder';

/**
 * StartAgent's config editor — how this workflow begins.
 *
 * Input Form fields reuse `SchemaBuilder`, the same row editor Structured
 * Output and WorkflowInputAgent already use (enum/list-of-enum included) —
 * plus a Label/Placeholder/Comes-from block attached only at the top level,
 * the same way WorkflowInputAgentConfig attaches "Comes from." File-typed
 * fields aren't FieldSpec-shaped (no file kind exists in that shared
 * vocabulary — see app/nodes/start.py), so they get their own small list
 * directly below, rather than teaching the shared schema compiler about
 * object storage.
 */

type Config = Record<string, unknown>;
type StartField = FieldSpec & { source?: string | null; label?: string; placeholder?: string };
type StartFileField = {
  name: string;
  label: string;
  required?: boolean;
  multiple?: boolean;
  accept?: string[];
  max_files?: number | null;
  source?: string | null;
};

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function nextFileFieldName(existing: StartFileField[]): string {
  const taken = new Set(existing.map(field => field.name));
  let number = existing.length + 1;
  while (taken.has(`file_${number}`)) number += 1;
  return `file_${number}`;
}

export function StartAgentConfig({
  config,
  onChange,
}: {
  config: Config;
  onChange: (next: Config) => void;
}) {
  const mode = config.mode === 'chatbot' ? 'chatbot' : 'input_form';
  const set = (patch: Config) => onChange({ ...config, ...patch });

  return (
    <div>
      <section className="mb-3 grid grid-cols-2 gap-1.5">
        <ModeCard
          active={mode === 'input_form'}
          description="Collect structured information before starting the workflow."
          label="Input Form"
          onSelect={() => set({ mode: 'input_form' })}
        />
        <ModeCard
          active={mode === 'chatbot'}
          description="Start the workflow from a conversational message."
          label="Chatbot Interface"
          onSelect={() => set({ mode: 'chatbot' })}
        />
      </section>

      {mode === 'input_form' ? (
        <InputFormFields config={config} onChange={onChange} />
      ) : (
        <ChatbotFields config={config} onChange={onChange} />
      )}
    </div>
  );
}

function InputFormFields({ config, onChange }: { config: Config; onChange: (next: Config) => void }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const fields = (config.fields as StartField[] | undefined) ?? [];
  const fileFields = (config.file_fields as StartFileField[] | undefined) ?? [];
  const sample = (config.sample as Record<string, unknown> | undefined) ?? {};

  return (
    <div>
      <label className="block text-[11px] font-medium text-ink-700">
        Form Title
        <input
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, title: event.target.value })}
          placeholder="Customer Support Request"
          value={asString(config.title)}
        />
      </label>
      <label className="mt-3 block text-[11px] font-medium text-ink-700">
        Description
        <textarea
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, description: event.target.value })}
          placeholder="Tell us how we can help."
          rows={2}
          value={asString(config.description)}
        />
      </label>

      <SchemaBuilder
        fields={fields as unknown as FieldSpec[]}
        helperText="Every field becomes a workflow output, addressed by downstream steps under its name."
        onChange={next => onChange({ ...config, fields: next as StartField[] })}
        title="Fields"
        topLevelExtra={(field, onFieldChange) => {
          const binding = field as StartField;
          return (
            <div className="space-y-2">
              <label className="block text-[11px] font-medium text-ink-700">
                Field Label
                <input
                  className="builder-field mt-1"
                  onChange={event => onFieldChange({ ...field, label: event.target.value } as FieldSpec)}
                  placeholder="Customer Question"
                  value={binding.label ?? ''}
                />
              </label>
              <label className="block text-[11px] font-medium text-ink-700">
                Placeholder
                <input
                  className="builder-field mt-1"
                  onChange={event => onFieldChange({ ...field, placeholder: event.target.value } as FieldSpec)}
                  placeholder="Describe your question..."
                  value={binding.placeholder ?? ''}
                />
              </label>
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
              </label>
            </div>
          );
        }}
      />

      <FileFieldsEditor
        fields={fileFields}
        onChange={next => onChange({ ...config, file_fields: next })}
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

      <section className="mt-4">
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setPreviewOpen(value => !value)}
          type="button"
        >
          {previewOpen ? 'Hide preview' : 'Preview form'}
        </button>
        {previewOpen && (
          <FormPreview description={asString(config.description)} fields={fields} fileFields={fileFields} title={asString(config.title)} />
        )}
      </section>
    </div>
  );
}

function FileFieldsEditor({
  fields,
  onChange,
}: {
  fields: StartFileField[];
  onChange: (next: StartFileField[]) => void;
}) {
  const replace = (index: number, patch: Partial<StartFileField>) => {
    const copy = [...fields];
    copy[index] = { ...copy[index], ...patch };
    onChange(copy);
  };

  return (
    <section className="mt-4">
      <div className="builder-panel-heading">File fields</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        File uploads — kept separate from the fields above since a file isn&apos;t
        one of the value types those rows describe.
      </p>
      <div className="mt-2 space-y-2">
        {fields.map((field, index) => (
          <div className="rounded-md border border-slate-200 p-2" key={index}>
            <div className="flex items-center gap-2">
              <input
                aria-label="Field name"
                className="builder-field flex-1 font-mono"
                onChange={event => replace(index, { name: event.target.value })}
                placeholder="field_name"
                value={field.name}
              />
              <button
                aria-label={`Remove ${field.name || 'field'}`}
                className="px-1 text-ink-400 hover:text-red-600"
                onClick={() => onChange(fields.filter((_, position) => position !== index))}
                type="button"
              >×</button>
            </div>
            <input
              aria-label="Field label"
              className="builder-field mt-1.5"
              onChange={event => replace(index, { label: event.target.value })}
              placeholder="Attachment"
              value={field.label}
            />
            <div className="mt-1.5 flex items-center gap-3 text-[11px] text-ink-600">
              <label className="flex items-center gap-1">
                <input
                  checked={field.required ?? false}
                  onChange={event => replace(index, { required: event.target.checked })}
                  type="checkbox"
                />
                Required
              </label>
              <label className="flex items-center gap-1">
                <input
                  checked={field.multiple ?? false}
                  onChange={event => replace(index, { multiple: event.target.checked, max_files: event.target.checked ? field.max_files : null })}
                  type="checkbox"
                />
                Multiple files
              </label>
            </div>
          </div>
        ))}
        <button
          className="w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
          onClick={() => onChange([...fields, { name: nextFileFieldName(fields), label: '', required: false, multiple: false }])}
          type="button"
        >
          + Add file field
        </button>
      </div>
    </section>
  );
}

function FormPreview({
  title, description, fields, fileFields,
}: {
  title: string;
  description: string;
  fields: StartField[];
  fileFields: StartFileField[];
}) {
  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-brand-softer p-3">
      {title && <div className="text-sm font-semibold text-ink-900">{title}</div>}
      {description && <p className="mt-0.5 text-[11px] text-ink-600">{description}</p>}
      <div className="mt-2 space-y-2">
        {fields.map((field, index) => (
          <label className="block text-[11px] font-medium text-ink-700" key={index}>
            {field.label || field.name}
            <PreviewWidget field={field} />
          </label>
        ))}
        {fileFields.map((field, index) => (
          <label className="block text-[11px] font-medium text-ink-700" key={index}>
            {field.label || field.name}
            <div className="builder-field mt-1 text-ink-500">
              {field.multiple ? 'Upload files…' : 'Upload file…'}
            </div>
          </label>
        ))}
      </div>
      <button className="ui-button ui-button--primary mt-3" disabled type="button">Submit</button>
    </div>
  );
}

function PreviewWidget({ field }: { field: StartField }) {
  const kind: FieldKind = field.type;
  if (kind === 'text') {
    return <textarea className="builder-field mt-1" disabled placeholder={field.placeholder} rows={2} />;
  }
  if (kind === 'boolean') {
    return <div className="mt-1"><input disabled type="checkbox" /></div>;
  }
  if (kind === 'date') {
    return <input className="builder-field mt-1" disabled type="date" />;
  }
  if (kind === 'enum' || (kind === 'list' && field.item_type === 'enum')) {
    const values = kind === 'enum' ? field.enum_values : field.item_enum_values;
    return (
      <select className="builder-field mt-1" disabled multiple={kind === 'list'}>
        {(values ?? []).map(value => <option key={value}>{value}</option>)}
      </select>
    );
  }
  if (kind === 'number' || kind === 'integer') {
    return <input className="builder-field mt-1" disabled placeholder={field.placeholder} type="number" />;
  }
  return <input className="builder-field mt-1" disabled placeholder={field.placeholder} />;
}

function ChatbotFields({ config, onChange }: { config: Config; onChange: (next: Config) => void }) {
  const suggested = (config.suggested_questions as string[] | undefined) ?? [];

  return (
    <div className="space-y-3">
      <label className="block text-[11px] font-medium text-ink-700">
        Chatbot Name
        <input
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, chatbot_name: event.target.value })}
          placeholder="Technical Support Assistant"
          value={asString(config.chatbot_name)}
        />
      </label>
      <label className="block text-[11px] font-medium text-ink-700">
        Welcome Message
        <textarea
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, welcome_message: event.target.value })}
          placeholder="Hello! How can I help you today?"
          rows={2}
          value={asString(config.welcome_message)}
        />
      </label>
      <label className="block text-[11px] font-medium text-ink-700">
        Message Placeholder
        <input
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, message_placeholder: event.target.value })}
          placeholder="Ask a question..."
          value={asString(config.message_placeholder, 'Ask a question...')}
        />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-ink-700">
        <input
          checked={config.allow_attachments !== false}
          onChange={event => onChange({ ...config, allow_attachments: event.target.checked })}
          type="checkbox"
        />
        Allow attachments
      </label>

      <section>
        <div className="builder-panel-heading">Suggested questions</div>
        <div className="mt-1 space-y-1">
          {suggested.map((question, index) => (
            <div className="flex gap-1" key={index}>
              <input
                className="builder-field flex-1"
                onChange={event => {
                  const next = [...suggested];
                  next[index] = event.target.value;
                  onChange({ ...config, suggested_questions: next });
                }}
                value={question}
              />
              <button
                aria-label={`Remove ${question}`}
                className="px-1 text-ink-400 hover:text-red-600"
                onClick={() => onChange({ ...config, suggested_questions: suggested.filter((_, i) => i !== index) })}
                type="button"
              >×</button>
            </div>
          ))}
        </div>
        <button
          className="mt-1 text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => onChange({ ...config, suggested_questions: [...suggested, ''] })}
          type="button"
        >
          + Add suggested question
        </button>
      </section>
    </div>
  );
}
