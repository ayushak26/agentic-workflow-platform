import { useEffect, useState } from 'react';

import type { WorkflowInputSpec } from './yaml-bridge';

const FILE_CATEGORIES = [
  ['pdf', 'PDF'],
  ['document', 'Documents'],
  ['markdown', 'Markdown'],
  ['presentation', 'Presentations'],
  ['spreadsheet', 'Spreadsheets'],
  ['code', 'Code files'],
  ['image', 'Images'],
] as const;

function nextInputName(inputs: Record<string, WorkflowInputSpec>): string {
  let number = Object.keys(inputs).length + 1;
  while (`input_${number}` in inputs) number += 1;
  return `input_${number}`;
}

function InputNameEditor({
  name,
  names,
  onCommit,
}: {
  name: string;
  names: string[];
  onCommit: (name: string) => void;
}) {
  const [draft, setDraft] = useState(name);
  const valid = /^[A-Za-z_][A-Za-z0-9_]*$/.test(draft);
  const duplicate = draft !== name && names.includes(draft);

  useEffect(() => setDraft(name), [name]);

  function commit() {
    if (valid && !duplicate) onCommit(draft);
    else setDraft(name);
  }

  return (
    <>
      <input
        value={draft}
        onChange={event => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={event => {
          if (event.key === 'Enter') {
            event.preventDefault();
            event.currentTarget.blur();
          }
        }}
        className={`mt-1 block w-full rounded-md border bg-white px-2 py-1.5 font-mono text-sm ${
          valid && !duplicate ? 'border-slate-300' : 'border-red-400'
        }`}
      />
      <p className={`mt-1 text-[11px] ${
        valid && !duplicate ? 'text-ink-500' : 'text-bad'
      }`}>
        {duplicate
          ? 'That input name is already used.'
          : 'Letters, numbers, and underscores; start with a letter.'}
      </p>
    </>
  );
}

export function WorkflowInputsPanel({
  inputs,
  onChange,
  onClose,
}: {
  inputs: Record<string, WorkflowInputSpec>;
  onChange: (inputs: Record<string, WorkflowInputSpec>) => void;
  onClose: () => void;
}) {
  function update(name: string, patch: Partial<WorkflowInputSpec>) {
    onChange({
      ...inputs,
      [name]: { ...inputs[name], ...patch },
    });
  }

  function rename(from: string, to: string) {
    if (
      to === from
      || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(to)
      || to in inputs
    ) return;
    const next: Record<string, WorkflowInputSpec> = {};
    for (const [name, spec] of Object.entries(inputs)) {
      next[name === from ? to : name] = spec;
    }
    onChange(next);
  }

  function remove(name: string) {
    const next = { ...inputs };
    delete next[name];
    onChange(next);
  }

  function addInput() {
    const name = nextInputName(inputs);
    onChange({
      ...inputs,
      [name]: {
        type: 'text',
        description: '',
        required: false,
      },
    });
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h3 className="font-medium text-ink-900">Workflow inputs</h3>
          <p className="text-xs text-ink-500">
            Define what users provide before a run.
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-lg leading-none text-ink-500 hover:text-ink-900"
        >
          ×
        </button>
      </div>

      <div className="space-y-4 p-4">
        {Object.keys(inputs).length === 0 && (
          <div className="rounded-md border border-dashed border-slate-300 p-4 text-center text-sm text-ink-500">
            No inputs yet. Add text, JSON, or file inputs.
          </div>
        )}

        {Object.entries(inputs).map(([name, spec]) => {
          const accepts = new Set(
            spec.accept ?? FILE_CATEGORIES.map(([value]) => value),
          );
          return (
            <section
              key={name}
              className="rounded-lg border border-slate-200 bg-slate-50 p-3"
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <label className="block text-xs font-medium text-ink-700">
                    Input name
                  </label>
                  <InputNameEditor
                    name={name}
                    names={Object.keys(inputs)}
                    onCommit={next => rename(name, next)}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => remove(name)}
                  className="mt-5 text-xs text-bad hover:underline"
                >
                  Remove
                </button>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-ink-700">
                    Type
                  </label>
                  <select
                    value={spec.type}
                    onChange={event => {
                      const type = event.target.value as WorkflowInputSpec['type'];
                      update(name, type === 'file'
                        ? {
                            type,
                            multiple: false,
                            max_files: 1,
                            accept: FILE_CATEGORIES.map(([value]) => value),
                          }
                        : {
                            type,
                            multiple: undefined,
                            max_files: undefined,
                            accept: undefined,
                          });
                    }}
                    className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                  >
                    <option value="text">Text</option>
                    <option value="json">JSON</option>
                    <option value="file">File</option>
                  </select>
                </div>
                <label className="mt-6 flex items-center gap-2 text-sm text-ink-700">
                  <input
                    type="checkbox"
                    checked={Boolean(spec.required)}
                    onChange={event => update(name, {
                      required: event.target.checked,
                    })}
                  />
                  Required
                </label>
              </div>

              <div className="mt-3">
                <label className="block text-xs font-medium text-ink-700">
                  Description
                </label>
                <textarea
                  rows={2}
                  value={spec.description ?? ''}
                  onChange={event => update(name, {
                    description: event.target.value,
                  })}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                />
              </div>

              {spec.type === 'file' && (
                <div className="mt-4 space-y-3 border-t border-slate-200 pt-3">
                  <div className="flex items-center justify-between gap-3">
                    <label className="flex items-center gap-2 text-sm text-ink-700">
                      <input
                        type="checkbox"
                        checked={Boolean(spec.multiple)}
                        onChange={event => update(name, {
                          multiple: event.target.checked,
                          max_files: event.target.checked ? 10 : 1,
                        })}
                      />
                      Allow multiple files
                    </label>
                    {spec.multiple && (
                      <label className="flex items-center gap-2 text-xs text-ink-500">
                        Max
                        <input
                          type="number"
                          min={1}
                          max={20}
                          value={spec.max_files ?? 10}
                          onChange={event => update(name, {
                            max_files: Math.max(
                              1,
                              Math.min(20, Number(event.target.value) || 1),
                            ),
                          })}
                          className="w-16 rounded border border-slate-300 bg-white px-2 py-1 text-sm"
                        />
                      </label>
                    )}
                  </div>

                  <div>
                    <div className="text-xs font-medium text-ink-700">
                      Accepted file types
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2">
                      {FILE_CATEGORIES.map(([value, label]) => (
                        <label
                          key={value}
                          className="flex items-center gap-2 text-xs text-ink-700"
                        >
                          <input
                            type="checkbox"
                            checked={accepts.has(value)}
                            onChange={event => {
                              const next = new Set(accepts);
                              if (event.target.checked) next.add(value);
                              else next.delete(value);
                              if (next.size > 0) {
                                update(name, { accept: Array.from(next) });
                              }
                            }}
                          />
                          {label}
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </section>
          );
        })}

        <button
          type="button"
          onClick={addInput}
          className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-accent-600 hover:bg-slate-50"
        >
          + Add workflow input
        </button>
      </div>
    </div>
  );
}
