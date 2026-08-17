import { useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type { ContractField, ContractNode, FieldSpec, OutputContract } from '../../../api/types';
import { FILE_CATEGORIES } from '../yaml-bridge';
import type { WorkflowInputSpec } from '../yaml-bridge';
import { resolveBinding, stepLabelFor } from './binding';
import type { DestinationKind } from './FieldPicker';
import { FieldPicker, ValuePicker } from './FieldPicker';
import { SchemaBuilder } from './SchemaBuilder';
import { TemplateTextField } from './TemplateTextField';

/**
 * The Prompt Template editor: Inputs → Instructions → Outputs.
 *
 * Nothing here shows `system_prompt`, `prompt_template` or `{{inputs.x}}`
 * syntax — those are generated underneath by the backend (see
 * `app.nodes.transform.TransformConfig`'s new-style fields) from exactly the
 * three sections below. A brand-new TransformAgent node gets this editor;
 * an existing one authored with a hand-written prompt_template keeps using
 * the generic SchemaForm (see ConfigureTab.tsx's `isLegacyTransform` check)
 * so nothing already saved is ever shown blank or silently reinterpreted.
 */

type Config = Record<string, unknown>;

type InputRow = {
  name: string;
  description?: string;
  type?: string;
  value?: string;
};

const INPUT_TYPES = [
  { value: 'string', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'boolean', label: 'Yes / no' },
  { value: 'date', label: 'Date' },
  { value: 'file', label: 'File' },
];

/** A File input's value isn't `{{inputs.x}}` — that resolves to a small
 *  storage-reference (file id/name/hash), not the document's text, which
 *  would silently hand the model metadata JSON instead of the file's
 *  content. It must instead point at a "File Reader" (WorkflowFileLoader)
 *  step's extracted `.text` output, which the author picks explicitly. */
function isFileReaderTextField(field: ContractField, node: ContractNode): boolean {
  return node.type_name === 'WorkflowFileLoader' && field.path === 'text';
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function nextInputName(existing: string[]): string {
  let number = existing.length + 1;
  while (existing.includes(`input_${number}`)) number += 1;
  return `input_${number}`;
}

function isValidIdentifier(name: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name);
}

function leafLabel(field: ContractField): string {
  const leaf = field.path.split('.').slice(-1)[0] || field.path;
  return leaf.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());
}

function destinationKindForType(type: string | undefined): DestinationKind {
  if (type === 'number') return 'number';
  if (type === 'boolean') return 'boolean';
  return 'text';
}

/**
 * The value editor for a single non-file Input row — same "resolve via
 * resolveBinding, render by binding.kind" pattern as DataMappingPanel's
 * FieldCard, so wiring a value from an earlier step feels identical whether
 * it's done here or from the Inputs tab. A File row keeps its own,
 * unrelated File Reader picker (see the `field.type === 'file'` branch
 * below) since a file's value isn't a plain {{inputs.x}} placeholder.
 */
function InputValueEditor({
  contract,
  field,
  onChange,
}: {
  contract: OutputContract | null;
  field: InputRow;
  onChange: (nextValue: string) => void;
}) {
  const [mode, setMode] = useState<'idle' | 'enter' | 'picker'>('idle');
  const binding = useMemo(() => resolveBinding(field.value, contract), [field.value, contract]);
  const destinationKind = destinationKindForType(field.type);
  const connected = binding.kind === 'resolved' || binding.kind === 'unresolved';
  const resetToDefault = () => onChange(`{{inputs.${field.name}}}`);

  return (
    <div className="mt-1.5">
      {binding.kind === 'resolved' && (
        <div>
          <div className="text-[11px] text-ink-800">
            <span className="text-ink-400">←</span>{' '}
            <span className="font-medium">{leafLabel(binding.field)}</span>
          </div>
          <div className="text-[10px] text-accent-700">From {stepLabelFor(binding)}</div>
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Change
            </button>
            <button className="text-[11px] font-medium text-ink-500 hover:underline" onClick={resetToDefault} type="button">
              Remove
            </button>
          </div>
          <details className="mt-1">
            <summary className="cursor-pointer text-[10px] text-ink-400">View reference</summary>
            <div className="mt-1 break-all rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink-600">
              {field.value}
            </div>
          </details>
        </div>
      )}

      {binding.kind === 'unresolved' && (
        <div>
          <div className="text-[11px] text-amber-700">
            ⚠ This value no longer exists in this workflow — the step or field
            it pointed to may have been renamed or removed.
          </div>
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Choose a new value
            </button>
            <button className="text-[11px] font-medium text-ink-500 hover:underline" onClick={resetToDefault} type="button">
              Remove
            </button>
          </div>
          <details className="mt-1">
            <summary className="cursor-pointer text-[10px] text-ink-400">View reference</summary>
            <div className="mt-1 break-all rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink-600">
              {binding.raw}
            </div>
          </details>
        </div>
      )}

      {binding.kind === 'literal' && mode !== 'enter' && (
        <div>
          <div className="rounded-md border border-ink-100 bg-brand-softer px-2 py-1.5 text-[11px] text-ink-700">
            {binding.value}
          </div>
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('enter')} type="button">
              Edit
            </button>
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Use previous step instead
            </button>
          </div>
        </div>
      )}

      {binding.kind === 'empty' && mode === 'idle' && (
        <div className="flex gap-2">
          <button className="ui-button ui-button--secondary" onClick={() => setMode('enter')} type="button">
            Enter a value
          </button>
          <button className="ui-button ui-button--secondary" onClick={() => setMode('picker')} type="button">
            Use previous step
          </button>
        </div>
      )}

      {mode === 'enter' && !connected && (
        <div>
          <input
            aria-label={`Value for ${field.name || 'input'}`}
            autoFocus
            className="builder-field"
            onChange={event => onChange(event.target.value)}
            placeholder="Type a value…"
            value={binding.kind === 'literal' ? binding.value : ''}
          />
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-ink-500 hover:underline" onClick={() => setMode('idle')} type="button">
              Done
            </button>
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Use previous step instead
            </button>
          </div>
        </div>
      )}

      {mode === 'picker' && (
        <div className="mt-1 rounded border border-slate-200 p-2">
          <ValuePicker
            contract={contract}
            destinationHint={field.description}
            destinationKind={destinationKind}
            destinationLabel={field.name}
            onPick={pickedField => { onChange(pickedField.reference); setMode('idle'); }}
            selectedReference={binding.kind === 'resolved' ? binding.field.reference : undefined}
          />
          <button className="mt-1 text-[11px] font-medium text-ink-500 hover:underline" onClick={() => setMode('idle')} type="button">
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

export function PromptTemplateConfig({
  config,
  contract,
  onChange,
  workflowInputs,
  onWorkflowInputsChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
  workflowInputs: Record<string, WorkflowInputSpec>;
  onWorkflowInputsChange: (inputs: Record<string, WorkflowInputSpec>) => void;
}) {
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [pickingFileSourceFor, setPickingFileSourceFor] = useState<number | null>(null);

  const inputFields = (config.input_fields as InputRow[] | undefined) ?? [];
  const outputFields = (config.output_fields as FieldSpec[] | undefined) ?? [];

  const set = (patch: Config) => onChange({ ...config, ...patch });
  const setInputFields = (rows: InputRow[]) => set({ input_fields: rows });

  const addInput = () => {
    const name = nextInputName(inputFields.map(row => row.name));
    setInputFields([
      ...inputFields,
      { name, description: '', type: 'string', value: `{{inputs.${name}}}` },
    ]);
    if (!(name in workflowInputs)) {
      onWorkflowInputsChange({
        ...workflowInputs,
        [name]: { type: 'text', required: false, description: '' },
      });
    }
  };

  const renameInput = (index: number, nextName: string) => {
    const trimmed = nextName.trim();
    const oldName = inputFields[index].name;
    if (
      trimmed === oldName
      || !isValidIdentifier(trimmed)
      || inputFields.some((row, i) => i !== index && row.name === trimmed)
    ) return;

    setInputFields(inputFields.map((row, i) => (
      i === index
        // A File input's value points at a File Reader step's extracted
        // text, not {{inputs.<name>}} — renaming it must not overwrite that
        // reference with the (meaningless, for a file) inputs placeholder.
        ? { ...row, name: trimmed, value: row.type === 'file' ? row.value : `{{inputs.${trimmed}}}` }
        : row
    )));

    if (oldName in workflowInputs && !(trimmed in workflowInputs)) {
      const next = { ...workflowInputs };
      next[trimmed] = next[oldName];
      delete next[oldName];
      onWorkflowInputsChange(next);
    }
  };

  const updateInput = (index: number, patch: Partial<InputRow>) => {
    setInputFields(inputFields.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    if ('description' in patch && inputFields[index].name in workflowInputs) {
      onWorkflowInputsChange({
        ...workflowInputs,
        [inputFields[index].name]: {
          ...workflowInputs[inputFields[index].name],
          description: patch.description,
        },
      });
    }
  };

  const setInputType = (index: number, type: string) => {
    const row = inputFields[index];
    if (type === 'file') {
      // Unlike the other types (cosmetic hints only), File genuinely changes
      // how the Run dialog collects this input and how the backend validates
      // it — so the workflow-level spec needs real file settings, and the
      // reference can no longer be the raw {{inputs.x}} placeholder (that
      // resolves to a storage reference, not the document's text).
      updateInput(index, { type, value: '' });
      onWorkflowInputsChange({
        ...workflowInputs,
        [row.name]: {
          ...workflowInputs[row.name],
          type: 'file',
          multiple: false,
          max_files: 1,
          accept: FILE_CATEGORIES.map(([value]) => value),
        },
      });
      return;
    }
    if (row.type === 'file') {
      onWorkflowInputsChange({
        ...workflowInputs,
        [row.name]: {
          type: 'text',
          required: workflowInputs[row.name]?.required ?? false,
          description: workflowInputs[row.name]?.description ?? '',
        },
      });
      updateInput(index, { type, value: `{{inputs.${row.name}}}` });
      return;
    }
    updateInput(index, { type });
  };

  const removeInput = (index: number) => {
    setInputFields(inputFields.filter((_, i) => i !== index));
    if (pickingFileSourceFor === index) setPickingFileSourceFor(null);
  };

  const draftInstructions = async () => {
    setDrafting(true);
    setDraftError(null);
    try {
      const result = await api.draftInstructions({
        existing_instructions: asString(config.instructions),
        input_fields: inputFields.map(field => ({
          name: field.name,
          description: field.description,
          type: field.type,
        })),
        output_fields: outputFields.map(field => ({
          name: field.name,
          description: field.description,
          type: field.type,
          enum_values: field.enum_values,
        })),
      });
      set({ instructions: result.answer });
    } catch (error) {
      setDraftError(error instanceof Error ? error.message : String(error));
    } finally {
      setDrafting(false);
    }
  };

  return (
    <div>
      <section>
        <div className="builder-panel-heading">Inputs</div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          The information this step receives. The platform wires each one in
          automatically — you never write a placeholder yourself.
        </p>

        <div className="mt-2 space-y-2">
          {inputFields.map((field, index) => (
            <div className="rounded-md border border-slate-200 p-2" key={index}>
              <div className="flex items-center gap-2">
                <input
                  aria-label="Variable name"
                  className="builder-field flex-1 font-mono"
                  onChange={event => renameInput(index, event.target.value)}
                  placeholder="variable_name"
                  value={field.name}
                />
                <select
                  aria-label={`Type of ${field.name || 'input'}`}
                  className="builder-field w-28 flex-none"
                  onChange={event => setInputType(index, event.target.value)}
                  value={field.type ?? 'string'}
                >
                  {INPUT_TYPES.map(item => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </select>
                <button
                  aria-label={`Remove ${field.name || 'input'}`}
                  className="flex-none px-1 text-ink-400 hover:text-red-600"
                  onClick={() => removeInput(index)}
                  type="button"
                >×</button>
              </div>
              <textarea
                aria-label={`Description of ${field.name || 'input'}`}
                className="builder-field mt-1.5"
                onChange={event => updateInput(index, { description: event.target.value })}
                placeholder="What this is, e.g. 'Subject of the customer message'"
                rows={1}
                value={field.description ?? ''}
              />

              {field.type !== 'file' && (
                <InputValueEditor
                  contract={contract}
                  field={field}
                  onChange={nextValue => updateInput(index, { value: nextValue })}
                />
              )}

              {field.type === 'file' && (
                <div className="mt-1.5 rounded border border-dashed border-slate-300 p-2">
                  {field.value ? (
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-mono text-[10px] text-accent-700">
                        {field.value}
                      </span>
                      <button
                        className="flex-none text-[10px] font-medium text-accent-700 hover:underline"
                        onClick={() => setPickingFileSourceFor(
                          value => (value === index ? null : index),
                        )}
                        type="button"
                      >
                        Change
                      </button>
                    </div>
                  ) : (
                    <button
                      className="w-full text-[11px] font-medium text-accent-700 hover:underline"
                      onClick={() => setPickingFileSourceFor(
                        value => (value === index ? null : index),
                      )}
                      type="button"
                    >
                      Pick the File Reader step this reads from
                    </button>
                  )}
                  {!field.value && (
                    <p className="mt-1 text-[10px] text-ink-500">
                      A file isn't text by itself — add a File Reader step to
                      extract its content, then point this Input at that
                      step's text.
                    </p>
                  )}
                  {pickingFileSourceFor === index && (
                    <div className="mt-2">
                      <FieldPicker
                        contract={contract}
                        emptyHint="No File Reader step found upstream of this one — add a File Reader step before this step, then come back to pick its extracted text."
                        filter={isFileReaderTextField}
                        onPick={pickedField => {
                          updateInput(index, { value: pickedField.reference });
                          setPickingFileSourceFor(null);
                        }}
                        selectedReference={field.value}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        <button
          className="mt-2 w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
          onClick={addInput}
          type="button"
        >
          + Add Input
        </button>
      </section>

      <section className="mt-4">
        <div className="flex items-center justify-between">
          <label className="block text-[11px] font-medium text-ink-700">
            Instructions
          </label>
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline disabled:opacity-50"
            disabled={drafting}
            onClick={draftInstructions}
            type="button"
          >
            {drafting ? 'Drafting…' : '✨ Draft Instructions'}
          </button>
        </div>
        <TemplateTextField
          aria-label="Instructions"
          contract={contract}
          onChange={text => set({ instructions: text })}
          placeholder={
            'Write the instructions for the AI here. You can also click "Draft Instructions" to generate a starting point from your Inputs and Outputs.'
          }
          rows={8}
          value={asString(config.instructions)}
        />
        {draftError && (
          <p className="mt-1 text-[11px] text-bad">{draftError}</p>
        )}
      </section>

      <SchemaBuilder
        fields={outputFields}
        onChange={fields => set({ output_fields: fields })}
      />
    </div>
  );
}
