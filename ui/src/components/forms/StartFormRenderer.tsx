import type { ReactNode } from 'react';

import type { FieldKind, FieldSpec } from '../../api/types';
import { evaluateConditionGroup, type FormConditionGroup } from '../../modes/studio/builder/formConditions';

/**
 * The one Start-form rendering implementation, used both by the Configure
 * tab's live preview (`interactive={false}`) and by RunDialog's real
 * fill-in form (`interactive={true}`) — per the form spec's own explicit
 * "reuse the runtime form components for preview, don't build a second
 * rendering implementation." Every catalog type from the Start-input
 * expansion renders here; nothing about a field's *authoring* (SchemaBuilder,
 * StartAgentConfig) lives in this file.
 */

export type StartFormField = FieldSpec & {
  name: string;
  label?: string;
  placeholder?: string;
  source?: string | null;
  kind?: 'field' | 'info' | 'readonly';
  section_title?: string | null;
  format?: 'email' | 'phone' | 'url' | 'currency' | 'percentage' | 'date' | 'time' | 'datetime';
  widget?: 'dropdown' | 'searchable_dropdown' | 'radio' | 'multi_select' | 'checkbox' | 'toggle';
  preset?: 'currency' | 'number_unit' | 'date_range' | 'duration' | 'address' | 'country';
  option_labels?: Record<string, string>;
  units?: string[];
  display?: 'table' | 'cards';
  min_length?: number;
  max_length?: number;
  pattern?: string;
  visible_when?: FormConditionGroup;
  required_when?: FormConditionGroup;
};

export type StartFormFileField = {
  name: string;
  label: string;
  required?: boolean;
  multiple?: boolean;
  accept?: string[];
  max_files?: number | null;
};

export type StartFormFileValue = { file_id: string; name: string } | Record<string, unknown>;

function labelFor(field: StartFormField): string {
  return field.label || field.name;
}

function optionLabel(field: StartFormField, value: string): string {
  return field.option_labels?.[value] ?? value;
}

export function isRequired(field: StartFormField, values: Record<string, unknown>): boolean {
  if (field.required_when && evaluateConditionGroup(field.required_when, values)) return true;
  return field.required !== false && !field.required_when;
}

function FieldWrapper({
  field,
  values,
  error,
  children,
}: {
  field: StartFormField;
  values: Record<string, unknown>;
  error?: string;
  children: ReactNode;
}) {
  // A repeating group or compound-preset row nests its own independently
  // labeled controls (table cells, per-child inputs) — wrapping all of that
  // in one outer <label> would give every nested button/input a mangled
  // accessible name (the whole row's text, not its own). Those render their
  // label as plain text instead; every other field is a single control, so
  // <label> still gets the usual click-to-focus behavior.
  const isComplex = (field.type === 'object' && Boolean(field.preset))
    || (field.type === 'list' && field.item_type === 'object');
  const labelText = (
    <>
      {labelFor(field)}
      {isRequired(field, values) && <span className="ml-0.5 text-red-500">*</span>}
    </>
  );
  return (
    <div>
      {field.section_title && (
        <div className="mb-2 mt-4 border-b border-slate-200 pb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-600 first:mt-0">
          {field.section_title}
        </div>
      )}
      {isComplex ? (
        <div className="text-[11px] font-medium text-ink-700">
          {labelText}
          {children}
        </div>
      ) : (
        <label className="block text-[11px] font-medium text-ink-700">
          {labelText}
          {children}
        </label>
      )}
      {field.description && (
        <p className="mt-0.5 text-[10px] text-ink-500">{field.description}</p>
      )}
      {error && (
        <p className="mt-0.5 text-[10px] text-red-600">{error}</p>
      )}
    </div>
  );
}

function TextInput({
  field, value, onChange, disabled,
}: { field: StartFormField; value: unknown; onChange: (next: string) => void; disabled: boolean }) {
  const inputType = field.format === 'email' ? 'email' : field.format === 'url' ? 'url' : field.format === 'phone' ? 'tel' : 'text';
  return (
    <input
      className="builder-field mt-1"
      disabled={disabled}
      maxLength={field.max_length}
      minLength={field.min_length}
      onChange={event => onChange(event.target.value)}
      pattern={field.pattern}
      placeholder={field.placeholder}
      type={inputType}
      value={typeof value === 'string' ? value : ''}
    />
  );
}

function EnumOptions({ field }: { field: StartFormField }) {
  const values = field.type === 'enum' ? field.enum_values : field.item_enum_values;
  return (
    <>
      {(values ?? []).map(value => (
        <option key={value} value={value}>{optionLabel(field, value)}</option>
      ))}
    </>
  );
}

function ScalarField({
  field, value, onChange, disabled,
}: { field: StartFormField; value: unknown; onChange: (next: unknown) => void; disabled: boolean }) {
  const kind: FieldKind = field.type;

  if (kind === 'text') {
    return (
      <textarea
        className="builder-field mt-1"
        disabled={disabled}
        maxLength={field.max_length}
        minLength={field.min_length}
        onChange={event => onChange(event.target.value)}
        placeholder={field.placeholder}
        rows={4}
        value={typeof value === 'string' ? value : ''}
      />
    );
  }

  if (kind === 'boolean') {
    if (field.widget === 'toggle') {
      return (
        <button
          aria-pressed={Boolean(value)}
          className={`mt-1 flex h-5 w-9 items-center rounded-full transition ${value ? 'bg-accent-600' : 'bg-slate-300'}`}
          disabled={disabled}
          onClick={() => onChange(!value)}
          type="button"
        >
          <span className={`h-4 w-4 rounded-full bg-white transition ${value ? 'translate-x-4' : 'translate-x-0.5'}`} />
        </button>
      );
    }
    return (
      <div className="mt-1">
        <input
          checked={Boolean(value)}
          disabled={disabled}
          onChange={event => onChange(event.target.checked)}
          type="checkbox"
        />
      </div>
    );
  }

  if (kind === 'date') {
    const inputType = field.format === 'time' ? 'time' : field.format === 'datetime' ? 'datetime-local' : 'date';
    return (
      <input
        className="builder-field mt-1"
        disabled={disabled}
        onChange={event => onChange(event.target.value)}
        type={inputType}
        value={typeof value === 'string' ? value : ''}
      />
    );
  }

  if (kind === 'enum') {
    if (field.widget === 'radio') {
      return (
        <div className="mt-1 space-y-1">
          {(field.enum_values ?? []).map(option => (
            <label className="flex items-center gap-1.5 text-[12px] font-normal text-ink-800" key={option}>
              <input
                checked={value === option}
                disabled={disabled}
                name={field.name}
                onChange={() => onChange(option)}
                type="radio"
              />
              {optionLabel(field, option)}
            </label>
          ))}
        </div>
      );
    }
    return (
      <select
        className="builder-field mt-1"
        disabled={disabled}
        onChange={event => onChange(event.target.value || null)}
        value={typeof value === 'string' ? value : ''}
      >
        <option value="">{field.widget === 'searchable_dropdown' ? 'Search…' : 'Select…'}</option>
        <EnumOptions field={field} />
      </select>
    );
  }

  if (kind === 'list' && field.item_type === 'enum') {
    const selected = Array.isArray(value) ? value as string[] : [];
    const toggle = (option: string) => {
      onChange(selected.includes(option) ? selected.filter(item => item !== option) : [...selected, option]);
    };
    return (
      <div className="mt-1 space-y-1">
        {(field.item_enum_values ?? []).map(option => (
          <label className="flex items-center gap-1.5 text-[12px] font-normal text-ink-800" key={option}>
            <input
              checked={selected.includes(option)}
              disabled={disabled}
              onChange={() => toggle(option)}
              type="checkbox"
            />
            {optionLabel(field, option)}
          </label>
        ))}
      </div>
    );
  }

  if (kind === 'number' || kind === 'integer') {
    return (
      <div className="mt-1 flex items-center gap-1.5">
        <input
          className="builder-field"
          disabled={disabled}
          max={field.format === 'percentage' ? (field.maximum ?? 100) : field.maximum ?? undefined}
          min={field.format === 'percentage' ? (field.minimum ?? 0) : field.minimum ?? undefined}
          onChange={event => onChange(event.target.value === '' ? null : Number(event.target.value))}
          placeholder={field.placeholder}
          step={kind === 'integer' ? 1 : 'any'}
          type="number"
          value={typeof value === 'number' ? value : ''}
        />
        {field.format === 'percentage' && <span className="text-ink-500">%</span>}
      </div>
    );
  }

  return <TextInput disabled={disabled} field={field} onChange={onChange} value={value} />;
}

function CompoundField({
  field, value, onChange, disabled,
}: { field: StartFormField; value: unknown; onChange: (next: unknown) => void; disabled: boolean }) {
  const record = (value && typeof value === 'object' && !Array.isArray(value)) ? value as Record<string, unknown> : {};
  const set = (key: string, next: unknown) => onChange({ ...record, [key]: next });
  const children = field.fields ?? [];

  if (field.preset === 'currency') {
    const amountField = children.find(child => child.name === 'amount');
    const currencyField = children.find(child => child.name === 'currency');
    return (
      <div className="mt-1 flex items-center gap-1.5">
        <input
          className="builder-field"
          disabled={disabled}
          onChange={event => set('amount', event.target.value === '' ? null : Number(event.target.value))}
          placeholder={amountField?.name}
          type="number"
          value={typeof record.amount === 'number' ? record.amount : ''}
        />
        <select
          className="builder-field w-24"
          disabled={disabled}
          onChange={event => set('currency', event.target.value || null)}
          value={typeof record.currency === 'string' ? record.currency : ''}
        >
          <option value="">—</option>
          {(field.units ?? currencyField?.enum_values ?? []).map(code => <option key={code} value={code}>{code}</option>)}
        </select>
      </div>
    );
  }

  if (field.preset === 'number_unit' || field.preset === 'duration') {
    const unitField = children.find(child => child.name === 'unit');
    return (
      <div className="mt-1 flex items-center gap-1.5">
        <input
          className="builder-field"
          disabled={disabled}
          onChange={event => set('value', event.target.value === '' ? null : Number(event.target.value))}
          type="number"
          value={typeof record.value === 'number' ? record.value : ''}
        />
        <select
          className="builder-field w-28"
          disabled={disabled}
          onChange={event => set('unit', event.target.value || null)}
          value={typeof record.unit === 'string' ? record.unit : ''}
        >
          <option value="">—</option>
          {(field.units ?? unitField?.enum_values ?? []).map(unit => <option key={unit} value={unit}>{unit}</option>)}
        </select>
      </div>
    );
  }

  if (field.preset === 'date_range') {
    return (
      <div className="mt-1 flex items-center gap-2">
        <div className="flex-1">
          <div className="text-[10px] text-ink-500">From</div>
          <input
            className="builder-field"
            disabled={disabled}
            onChange={event => set('start', event.target.value || null)}
            type="date"
            value={typeof record.start === 'string' ? record.start : ''}
          />
        </div>
        <div className="flex-1">
          <div className="text-[10px] text-ink-500">To</div>
          <input
            className="builder-field"
            disabled={disabled}
            onChange={event => set('end', event.target.value || null)}
            type="date"
            value={typeof record.end === 'string' ? record.end : ''}
          />
        </div>
      </div>
    );
  }

  if (field.preset === 'address') {
    const addressFields: Array<[string, string]> = [
      ['street', 'Street'], ['house_number', 'House Number'],
      ['postal_code', 'Postal Code'], ['city', 'City'], ['country', 'Country'],
    ];
    return (
      <div className="mt-1 grid grid-cols-2 gap-2">
        {addressFields.map(([key, addrLabel]) => (
          <label className="text-[10px] font-medium text-ink-600" key={key}>
            {addrLabel}
            <input
              className="builder-field mt-0.5"
              disabled={disabled}
              onChange={event => set(key, event.target.value || null)}
              value={typeof record[key] === 'string' ? record[key] as string : ''}
            />
          </label>
        ))}
      </div>
    );
  }

  // Generic object fallback — a group with no recognized preset.
  return (
    <div className="mt-1 space-y-2 rounded border border-slate-200 p-2">
      {children.map(child => (
        <label className="block text-[10px] font-medium text-ink-600" key={child.name}>
          {child.name}
          <input
            className="builder-field mt-0.5"
            disabled={disabled}
            onChange={event => set(child.name, event.target.value || null)}
            value={typeof record[child.name] === 'string' ? record[child.name] as string : ''}
          />
        </label>
      ))}
    </div>
  );
}

function RepeatingGroupField({
  field, value, onChange, disabled,
}: { field: StartFormField; value: unknown; onChange: (next: unknown) => void; disabled: boolean }) {
  const rows = Array.isArray(value) ? value as Record<string, unknown>[] : [];
  const children = field.fields ?? [];
  const asTable = field.display === 'table' || (field.display !== 'cards' && children.every(child => child.type !== 'object' && child.type !== 'list'));

  const setCell = (rowIndex: number, key: string, next: unknown) => {
    const copy = rows.map(row => ({ ...row }));
    copy[rowIndex] = { ...copy[rowIndex], [key]: next };
    onChange(copy);
  };
  const addRow = () => onChange([...rows, {}]);
  const removeRow = (rowIndex: number) => onChange(rows.filter((_, index) => index !== rowIndex));

  if (asTable) {
    return (
      <div className="mt-1 overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-ink-500">
              {children.map(child => <th className="pb-1 pr-2 font-medium" key={child.name}>{child.name}</th>)}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {children.map(child => (
                  <td className="pb-1 pr-2" key={child.name}>
                    <input
                      className="builder-field"
                      disabled={disabled}
                      onChange={event => setCell(
                        rowIndex,
                        child.name,
                        child.type === 'number' || child.type === 'integer'
                          ? (event.target.value === '' ? null : Number(event.target.value))
                          : event.target.value,
                      )}
                      type={child.type === 'number' || child.type === 'integer' ? 'number' : child.type === 'date' ? 'date' : 'text'}
                      value={row[child.name] == null ? '' : String(row[child.name])}
                    />
                  </td>
                ))}
                <td>
                  {!disabled && (
                    <button aria-label={`Remove row ${rowIndex + 1}`} className="text-ink-400 hover:text-red-600" onClick={() => removeRow(rowIndex)} type="button">×</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!disabled && (
          <button className="mt-1 text-[11px] font-medium text-accent-700 hover:underline" onClick={addRow} type="button">
            + Add Row
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mt-1 space-y-2">
      {rows.map((row, rowIndex) => (
        <div className="rounded border border-slate-200 p-2" key={rowIndex}>
          <div className="grid grid-cols-2 gap-2">
            {children.map(child => (
              <label className="text-[10px] font-medium text-ink-600" key={child.name}>
                {child.name}
                <input
                  className="builder-field mt-0.5"
                  disabled={disabled}
                  onChange={event => setCell(rowIndex, child.name, event.target.value)}
                  value={row[child.name] == null ? '' : String(row[child.name])}
                />
              </label>
            ))}
          </div>
          {!disabled && (
            <button className="mt-1.5 text-[11px] font-medium text-ink-500 hover:text-red-600" onClick={() => removeRow(rowIndex)} type="button">
              Remove
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <button className="w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50" onClick={addRow} type="button">
          + Add Another
        </button>
      )}
    </div>
  );
}

function FileFieldWidget({
  field, disabled,
}: { field: StartFormFileField; disabled: boolean }) {
  return (
    <div className="builder-field mt-1 text-ink-500">
      {disabled ? (field.multiple ? 'Upload files…' : 'Upload file…') : (
        <input disabled={disabled} multiple={field.multiple} type="file" />
      )}
    </div>
  );
}

export function StartFormRenderer({
  title,
  description,
  fields,
  fileFields,
  values,
  onChange,
  interactive,
  errors,
}: {
  title?: string;
  description?: string;
  fields: StartFormField[];
  fileFields: StartFormFileField[];
  values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
  interactive: boolean;
  errors?: Record<string, string>;
}) {
  const disabled = !interactive;

  return (
    <div className="space-y-3">
      {title && <div className="text-sm font-semibold text-ink-900">{title}</div>}
      {description && <p className="text-[11px] text-ink-600">{description}</p>}

      {fields.map(field => {
        if (field.visible_when && !evaluateConditionGroup(field.visible_when, values)) return null;

        if (field.kind === 'info') {
          return (
            <div key={field.name}>
              {field.section_title && (
                <div className="mb-2 mt-4 border-b border-slate-200 pb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-600 first:mt-0">
                  {field.section_title}
                </div>
              )}
              {field.label && <div className="text-[12px] font-semibold text-ink-800">{field.label}</div>}
              {field.description && <p className="mt-0.5 text-[11px] text-ink-600">{field.description}</p>}
            </div>
          );
        }

        const fieldDisabled = disabled || field.kind === 'readonly';
        const value = values[field.name];
        const setValue = (next: unknown) => onChange(field.name, next);

        return (
          <FieldWrapper error={errors?.[field.name]} field={field} key={field.name} values={values}>
            {field.type === 'object' && field.preset
              ? <CompoundField disabled={fieldDisabled} field={field} onChange={setValue} value={value} />
              : field.type === 'list' && field.item_type === 'object'
                ? <RepeatingGroupField disabled={fieldDisabled} field={field} onChange={setValue} value={value} />
                : <ScalarField disabled={fieldDisabled} field={field} onChange={setValue} value={value} />}
          </FieldWrapper>
        );
      })}

      {fileFields.map(field => (
        <label className="block text-[11px] font-medium text-ink-700" key={field.name}>
          {field.label || field.name}
          {field.required && <span className="ml-0.5 text-red-500">*</span>}
          <FileFieldWidget disabled={disabled} field={field} />
        </label>
      ))}
    </div>
  );
}
