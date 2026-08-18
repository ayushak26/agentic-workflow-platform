import { useState } from 'react';

import type { FieldSpec } from '../../../api/types';
import { StartFormRenderer, type StartFormField, type StartFormFileField } from '../../../components/forms/StartFormRenderer';
import { DefaultsEditor } from './ConfigureTab';
import type { FormCondition, FormConditionGroup, FormConditionOperator } from './formConditions';
import { ModeCard } from './RouterEditor';
import { SchemaBuilder } from './SchemaBuilder';

/**
 * StartAgent's config editor — how this workflow begins.
 *
 * Input Form fields reuse `SchemaBuilder`, the same row editor Structured
 * Output and WorkflowInputAgent already use (enum/list-of-enum included) —
 * plus a contextual block attached only at the top level (Field Label,
 * Placeholder, "Comes from", and every form-authoring extension below),
 * mirroring WorkflowInputAgentConfig's "Comes from" attachment. File-typed
 * fields aren't FieldSpec-shaped (no file kind exists in that shared
 * vocabulary — see app/nodes/start.py), so they get their own small list.
 *
 * Every "new" catalog type is a widget/preset hint over an existing
 * FieldSpec storage kind (see app/nodes/workflow_input.py's InputFieldBinding
 * docstring) — the "+ Quick add" buttons below just write the correct
 * pre-filled shape, the author never hand-builds a currency/address object.
 */

type Config = Record<string, unknown>;
type StartField = StartFormField;

const CURRENCY_CODES = ['EUR', 'USD', 'GBP'];

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function nextName(existing: Array<{ name: string }>, base = 'field'): string {
  const taken = new Set(existing.map(item => item.name));
  let number = existing.length + 1;
  while (taken.has(`${base}_${number}`)) number += 1;
  return `${base}_${number}`;
}

function nextFileFieldName(existing: StartFormFileField[]): string {
  return nextName(existing, 'file');
}

// ---- Quick-add presets: each writes the exact shape StartFormRenderer and
// the backend's compound-preset validation already know how to handle. ----

function currencyPreset(name: string): StartField {
  return {
    name, label: 'Estimated Budget', type: 'object', preset: 'currency',
    required: false, units: CURRENCY_CODES,
    fields: [
      { name: 'amount', type: 'number', required: true },
      { name: 'currency', type: 'enum', required: true, enum_values: CURRENCY_CODES },
    ],
  };
}

function numberUnitPreset(name: string): StartField {
  return {
    name, label: 'Flow Rate', type: 'object', preset: 'number_unit',
    required: false, units: ['m3/h', 'l/min', 'bar', 'kg', 'm'],
    fields: [
      { name: 'value', type: 'number', required: true },
      { name: 'unit', type: 'enum', required: true, enum_values: ['m3/h', 'l/min', 'bar', 'kg', 'm'] },
    ],
  };
}

function durationPreset(name: string): StartField {
  return {
    name, label: 'Estimated Downtime', type: 'object', preset: 'duration',
    required: false, units: ['minutes', 'hours', 'days'],
    fields: [
      { name: 'value', type: 'number', required: true },
      { name: 'unit', type: 'enum', required: true, enum_values: ['minutes', 'hours', 'days'] },
    ],
  };
}

function dateRangePreset(name: string): StartField {
  return {
    name, label: 'Required Period', type: 'object', preset: 'date_range', required: false,
    fields: [
      { name: 'start', type: 'date', required: true },
      { name: 'end', type: 'date', required: true },
    ],
  };
}

function addressPreset(name: string): StartField {
  return {
    name, label: 'Address', type: 'object', preset: 'address', required: false,
    fields: [
      { name: 'street', type: 'string', required: true },
      { name: 'house_number', type: 'string', required: false },
      { name: 'postal_code', type: 'string', required: true },
      { name: 'city', type: 'string', required: true },
      { name: 'country', type: 'string', required: true },
    ],
  };
}

const COUNTRIES: Array<[string, string]> = [
  ['NL', 'Netherlands'], ['DE', 'Germany'], ['BE', 'Belgium'], ['FR', 'France'],
  ['GB', 'United Kingdom'], ['US', 'United States'], ['ES', 'Spain'], ['IT', 'Italy'],
];

function countryPreset(name: string): StartField {
  return {
    name, label: 'Country', type: 'enum', preset: 'country', required: false,
    widget: 'searchable_dropdown',
    enum_values: COUNTRIES.map(([code]) => code),
    option_labels: Object.fromEntries(COUNTRIES),
  };
}

function repeatingGroupPreset(name: string): StartField {
  return {
    name, label: 'Products Requested', type: 'list', item_type: 'object', required: false,
    display: 'table',
    fields: [
      { name: 'product', type: 'string', required: true },
      { name: 'quantity', type: 'integer', required: true },
      { name: 'required_date', type: 'date', required: false },
    ],
  };
}

function sectionPreset(name: string): StartField {
  return { name, label: '', type: 'string', kind: 'info', section_title: 'New Section', required: false };
}

function infoPreset(name: string): StartField {
  return { name, label: 'Information', description: 'Helpful context for whoever fills this in.', type: 'string', kind: 'info', required: false };
}

function readonlyPreset(name: string): StartField {
  return { name, label: 'Reference', type: 'string', kind: 'readonly', required: false };
}

const QUICK_ADD: Array<{ category: string; label: string; build: (name: string) => StartField }> = [
  { category: 'Numbers', label: 'Currency', build: currencyPreset },
  { category: 'Numbers', label: 'Number + Unit', build: numberUnitPreset },
  { category: 'Date & Time', label: 'Date Range', build: dateRangePreset },
  { category: 'Date & Time', label: 'Duration', build: durationPreset },
  { category: 'Selection', label: 'Country', build: countryPreset },
  { category: 'Structured', label: 'Address', build: addressPreset },
  { category: 'Structured', label: 'Repeating Group / Line Items', build: repeatingGroupPreset },
  { category: 'Layout', label: 'Section', build: sectionPreset },
  { category: 'Layout', label: 'Information Block', build: infoPreset },
  { category: 'Layout', label: 'Read-only Value', build: readonlyPreset },
];

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
  const [quickAddOpen, setQuickAddOpen] = useState(false);
  const fields = (config.fields as StartField[] | undefined) ?? [];
  const fileFields = (config.file_fields as StartFormFileField[] | undefined) ?? [];
  const sample = (config.sample as Record<string, unknown> | undefined) ?? {};

  const quickAddCategories = [...new Set(QUICK_ADD.map(item => item.category))];

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
        helperText="Every field becomes a workflow output, addressed by downstream steps under its name. Use “+ Add field” for basic/selection/number/date fields, or the quick-add menu below for currency, addresses, repeating groups, sections and more."
        onChange={next => onChange({ ...config, fields: next as StartField[] })}
        title="Fields"
        topLevelExtra={(field, onFieldChange) => (
          <FieldExtras
            allFields={fields}
            field={field as StartField}
            onChange={onFieldChange}
          />
        )}
      />

      <div className="mt-2">
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setQuickAddOpen(value => !value)}
          type="button"
        >
          {quickAddOpen ? 'Hide quick-add' : '+ Quick add (Currency, Address, Repeating Group, Section…)'}
        </button>
        {quickAddOpen && (
          <div className="mt-2 space-y-2 rounded-md border border-slate-200 p-2">
            {quickAddCategories.map(category => (
              <div key={category}>
                <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">{category}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {QUICK_ADD.filter(item => item.category === category).map(item => (
                    <button
                      className="rounded border border-slate-200 px-2 py-1 text-[11px] text-ink-700 hover:border-accent-400"
                      key={item.label}
                      onClick={() => {
                        onChange({ ...config, fields: [...fields, item.build(nextName(fields))] });
                        setQuickAddOpen(false);
                      }}
                      type="button"
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <FileFieldsEditor
        fields={fileFields}
        onChange={next => onChange({ ...config, file_fields: next })}
      />

      <section className="mt-4">
        <div className="builder-panel-heading">Default values</div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          Used as a fallback whenever a field has no real value yet — including
          Test/Simulate, but a real value always wins.
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
          <div className="mt-2 rounded-lg border border-slate-200 bg-brand-softer p-3">
            <StartFormRenderer
              description={asString(config.description)}
              fields={fields}
              fileFields={fileFields}
              interactive={false}
              onChange={() => undefined}
              title={asString(config.title)}
              values={sample}
            />
            <button className="ui-button ui-button--primary mt-3" disabled type="button">Submit</button>
          </div>
        )}
      </section>
    </div>
  );
}

function FieldExtras({
  field,
  allFields,
  onChange,
}: {
  field: StartField;
  allFields: StartField[];
  onChange: (next: FieldSpec) => void;
}) {
  const set = (patch: Partial<StartField>) => onChange({ ...field, ...patch } as FieldSpec);
  const isInfo = field.kind === 'info';
  const index = allFields.indexOf(field);
  const earlierFields = index >= 0 ? allFields.slice(0, index) : [];

  return (
    <div className="space-y-2">
      {!isInfo && (
        <>
          <label className="block text-[11px] font-medium text-ink-700">
            Field Label
            <input
              className="builder-field mt-1"
              onChange={event => set({ label: event.target.value })}
              placeholder="Customer Question"
              value={field.label ?? ''}
            />
          </label>
          <label className="block text-[11px] font-medium text-ink-700">
            Placeholder
            <input
              className="builder-field mt-1"
              onChange={event => set({ placeholder: event.target.value })}
              placeholder="Describe your question..."
              value={field.placeholder ?? ''}
            />
          </label>
          <label className="block text-[11px] font-medium text-ink-700">
            Comes from
            <input
              className="builder-field mt-1 font-mono"
              onChange={event => set({ source: event.target.value || null })}
              placeholder={`inputs.${field.name || 'field_name'}`}
              value={field.source ?? ''}
            />
          </label>
        </>
      )}

      {isInfo && (
        <label className="block text-[11px] font-medium text-ink-700">
          Heading
          <input
            className="builder-field mt-1"
            onChange={event => set({ label: event.target.value })}
            placeholder="Service Information"
            value={field.label ?? ''}
          />
        </label>
      )}

      <label className="block text-[11px] font-medium text-ink-700">
        Section break before this field <span className="font-normal text-ink-400">Optional</span>
        <input
          className="builder-field mt-1"
          onChange={event => set({ section_title: event.target.value || null })}
          placeholder="Customer Information"
          value={field.section_title ?? ''}
        />
      </label>

      {!isInfo && (
        <label className="flex items-center gap-2 text-[11px] text-ink-700">
          <input
            checked={field.kind === 'readonly'}
            onChange={event => set({ kind: event.target.checked ? 'readonly' : 'field' })}
            type="checkbox"
          />
          Read-only (not editable in the form)
        </label>
      )}

      {!isInfo && (field.type === 'string' || field.type === 'text') && (
        <FormatAndLengthControls field={field} onChange={set} />
      )}
      {!isInfo && field.type === 'number' && (
        <label className="flex items-center gap-2 text-[11px] text-ink-700">
          <input
            checked={field.format === 'percentage'}
            onChange={event => set({ format: event.target.checked ? 'percentage' : undefined })}
            type="checkbox"
          />
          Show as a percentage
        </label>
      )}
      {!isInfo && field.type === 'date' && (
        <label className="block text-[11px] font-medium text-ink-700">
          Shows
          <select
            className="builder-field mt-1"
            onChange={event => set({ format: event.target.value as StartField['format'] })}
            value={field.format ?? 'date'}
          >
            <option value="date">Date</option>
            <option value="time">Time</option>
            <option value="datetime">Date &amp; Time</option>
          </select>
        </label>
      )}
      {!isInfo && field.type === 'boolean' && (
        <label className="block text-[11px] font-medium text-ink-700">
          Shows as
          <select
            className="builder-field mt-1"
            onChange={event => set({ widget: event.target.value as StartField['widget'] })}
            value={field.widget === 'toggle' ? 'toggle' : 'checkbox'}
          >
            <option value="checkbox">Checkbox</option>
            <option value="toggle">Toggle</option>
          </select>
        </label>
      )}
      {!isInfo && (field.type === 'enum' || (field.type === 'list' && field.item_type === 'enum')) && (
        <EnumWidgetControls field={field} onChange={set} />
      )}
      {!isInfo && field.type === 'list' && field.item_type === 'object' && (
        <label className="block text-[11px] font-medium text-ink-700">
          Display as
          <select
            className="builder-field mt-1"
            onChange={event => set({ display: event.target.value as StartField['display'] })}
            value={field.display ?? 'table'}
          >
            <option value="table">Line Items (compact table)</option>
            <option value="cards">Repeating Group (stacked cards)</option>
          </select>
        </label>
      )}

      {!isInfo && (
        <ConditionEditor
          earlierFields={earlierFields}
          field={field}
          onChange={set}
        />
      )}
    </div>
  );
}

function FormatAndLengthControls({
  field, onChange,
}: { field: StartField; onChange: (patch: Partial<StartField>) => void }) {
  return (
    <div className="space-y-2 rounded-md border border-slate-100 p-2">
      <label className="block text-[11px] font-medium text-ink-700">
        Format
        <select
          className="builder-field mt-1"
          onChange={event => onChange({ format: (event.target.value || undefined) as StartField['format'] })}
          value={field.format ?? ''}
        >
          <option value="">Plain text</option>
          <option value="email">Email</option>
          <option value="phone">Phone</option>
          <option value="url">Website / URL</option>
        </select>
      </label>
      <div className="flex gap-2">
        <label className="flex-1 text-[11px] font-medium text-ink-700">
          Min length
          <input
            className="builder-field mt-1"
            onChange={event => onChange({ min_length: event.target.value === '' ? undefined : Number(event.target.value) })}
            type="number"
            value={field.min_length ?? ''}
          />
        </label>
        <label className="flex-1 text-[11px] font-medium text-ink-700">
          Max length
          <input
            className="builder-field mt-1"
            onChange={event => onChange({ max_length: event.target.value === '' ? undefined : Number(event.target.value) })}
            type="number"
            value={field.max_length ?? ''}
          />
        </label>
      </div>
    </div>
  );
}

function EnumWidgetControls({
  field, onChange,
}: { field: StartField; onChange: (patch: Partial<StartField>) => void }) {
  const values = field.type === 'enum' ? (field.enum_values ?? []) : (field.item_enum_values ?? []);
  const isMulti = field.type === 'list';
  const optionLabels = field.option_labels ?? {};

  return (
    <div className="space-y-2 rounded-md border border-slate-100 p-2">
      {!isMulti && (
        <label className="block text-[11px] font-medium text-ink-700">
          Shows as
          <select
            className="builder-field mt-1"
            onChange={event => onChange({ widget: (event.target.value || undefined) as StartField['widget'] })}
            value={field.widget ?? 'dropdown'}
          >
            <option value="dropdown">Dropdown</option>
            <option value="searchable_dropdown">Searchable Dropdown</option>
            <option value="radio">Radio buttons</option>
          </select>
        </label>
      )}
      {values.length > 0 && (
        <div>
          <div className="text-[11px] font-medium text-ink-700">Display labels</div>
          <p className="text-[10px] text-ink-500">The stored value never changes; only what's shown.</p>
          <div className="mt-1 space-y-1">
            {values.map(value => (
              <div className="flex items-center gap-1.5" key={value}>
                <span className="w-24 flex-none truncate font-mono text-[10px] text-ink-500">{value}</span>
                <input
                  className="builder-field flex-1"
                  onChange={event => onChange({ option_labels: { ...optionLabels, [value]: event.target.value } })}
                  placeholder={value}
                  value={optionLabels[value] ?? ''}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const CONDITION_OPERATORS: Array<[FormConditionOperator, string]> = [
  ['equals', 'equals'], ['not_equals', 'does not equal'], ['contains', 'contains'], ['in', 'is one of'],
];

function toGroup(condition: FormCondition | null): FormConditionGroup | undefined {
  return condition ? { operator: 'and', conditions: [condition] } : undefined;
}

function firstCondition(group: FormConditionGroup | undefined): FormCondition | null {
  const first = group?.conditions[0];
  return first && !('conditions' in first) ? first : null;
}

function ConditionEditor({
  field,
  earlierFields,
  onChange,
}: {
  field: StartField;
  earlierFields: StartField[];
  onChange: (patch: Partial<StartField>) => void;
}) {
  if (earlierFields.length === 0) return null;

  const visible = firstCondition(field.visible_when);
  const required = firstCondition(field.required_when);

  const conditionRow = (
    label: string,
    hint: string,
    current: FormCondition | null,
    setGroup: (next: FormConditionGroup | undefined) => void,
  ) => (
    <div>
      <label className="flex items-center gap-2 text-[11px] text-ink-700">
        <input
          checked={current !== null}
          onChange={event => setGroup(event.target.checked
            ? { operator: 'and', conditions: [{ field: earlierFields[0].name, operator: 'equals', value: '' }] }
            : undefined)}
          type="checkbox"
        />
        {label}
        <span className="font-normal text-ink-400">{hint}</span>
      </label>
      {current && (
        <div className="mt-1 flex items-center gap-1.5 pl-5">
          <select
            className="builder-field"
            onChange={event => setGroup(toGroup({ ...current, field: event.target.value }))}
            value={current.field}
          >
            {earlierFields.map(item => <option key={item.name} value={item.name}>{item.label || item.name}</option>)}
          </select>
          <select
            className="builder-field"
            onChange={event => setGroup(toGroup({ ...current, operator: event.target.value as FormConditionOperator }))}
            value={current.operator}
          >
            {CONDITION_OPERATORS.map(([value, opLabel]) => <option key={value} value={value}>{opLabel}</option>)}
          </select>
          <input
            className="builder-field flex-1"
            onChange={event => setGroup(toGroup({ ...current, value: event.target.value }))}
            value={typeof current.value === 'string' ? current.value : ''}
          />
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-2 rounded-md border border-slate-100 p-2">
      <div className="text-[11px] font-medium text-ink-700">Conditional</div>
      {conditionRow('Show only when…', '(otherwise always visible)', visible, next => onChange({ visible_when: next }))}
      {conditionRow('Require only when…', '(on top of Required above)', required, next => onChange({ required_when: next }))}
    </div>
  );
}

function FileFieldsEditor({
  fields,
  onChange,
}: {
  fields: StartFormFileField[];
  onChange: (next: StartFormFileField[]) => void;
}) {
  const replace = (index: number, patch: Partial<StartFormFileField>) => {
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
