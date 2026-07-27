import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../../api/client';
import type { WorkflowFileCapabilities } from '../../api/types';
import type { WorkflowInputSpec } from './yaml-bridge';

const FALLBACK_CATEGORIES: Record<string, string[]> = {
  pdf: ['.pdf'],
  document: ['.doc', '.docx', '.odt', '.rtf', '.txt'],
  markdown: ['.md', '.markdown'],
  presentation: ['.ppt', '.pptx', '.odp'],
  spreadsheet: ['.csv', '.xls', '.xlsx', '.ods'],
  code: [
    '.c', '.cfg', '.cpp', '.cs', '.css', '.go', '.h', '.hpp', '.html',
    '.ini', '.ipynb', '.java', '.js', '.json', '.jsx', '.kt', '.php',
    '.properties', '.py', '.r', '.rb', '.rs', '.scala', '.sh', '.sql',
    '.svelte', '.swift', '.tf', '.toml', '.ts', '.tsx', '.vue', '.xml',
    '.yaml', '.yml',
  ],
  image: [
    '.bmp', '.gif', '.heic', '.jpeg', '.jpg', '.png', '.svg', '.tif',
    '.tiff', '.webp',
  ],
};

const FALLBACK_CAPABILITIES: WorkflowFileCapabilities = {
  categories: FALLBACK_CATEGORIES,
  extensions: Object.values(FALLBACK_CATEGORIES).flat(),
  max_file_size_bytes: 50 * 1024 * 1024,
  max_files_per_input: 20,
};

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function acceptedExtensions(
  spec: WorkflowInputSpec,
  capabilities: WorkflowFileCapabilities,
): string[] {
  const accepts = spec.accept?.length
    ? spec.accept
    : Object.keys(capabilities.categories);
  return Array.from(new Set(
    accepts.flatMap(item => (
      item.startsWith('.')
        ? [item.toLowerCase()]
        : (capabilities.categories[item] ?? [])
    )),
  ));
}

function FileInputField({
  inputId,
  inputName,
  spec,
  files,
  capabilities,
  error,
  onChange,
}: {
  inputId: string;
  inputName: string;
  spec: WorkflowInputSpec;
  files: File[];
  capabilities: WorkflowFileCapabilities;
  error?: string;
  onChange: (files: File[], error?: string) => void;
}) {
  const extensions = useMemo(
    () => acceptedExtensions(spec, capabilities),
    [spec, capabilities],
  );
  const allowed = useMemo(() => new Set(extensions), [extensions]);
  const maximum = spec.multiple
    ? Math.min(
        spec.max_files ?? capabilities.max_files_per_input,
        capabilities.max_files_per_input,
      )
    : 1;

  function addFiles(incoming: File[]) {
    const next = spec.multiple ? [...files, ...incoming] : incoming.slice(0, 1);
    const unique = Array.from(
      new Map(next.map(file => [`${file.name}:${file.size}`, file])).values(),
    );
    const invalid = unique.find(file => !allowed.has(extensionOf(file.name)));
    if (invalid) {
      onChange(
        files,
        `${invalid.name} is not an accepted file type for ${inputName}.`,
      );
      return;
    }
    const tooLarge = unique.find(
      file => file.size > capabilities.max_file_size_bytes,
    );
    if (tooLarge) {
      onChange(
        files,
        `${tooLarge.name} exceeds the ${formatBytes(
          capabilities.max_file_size_bytes,
        )} limit.`,
      );
      return;
    }
    if (unique.length > maximum) {
      onChange(files, `${inputName} accepts at most ${maximum} file(s).`);
      return;
    }
    onChange(unique);
  }

  return (
    <div>
      <label className="block text-sm font-medium text-ink-700">
        {inputName}
        {spec.required && <span className="ml-1 text-bad">*</span>}
        <span className="ml-2 text-xs font-normal text-ink-500">
          ({spec.multiple ? 'files' : 'file'})
        </span>
      </label>
      {spec.description && (
        <p className="text-xs text-ink-500 mb-2">{spec.description}</p>
      )}

      <label
        htmlFor={inputId}
        onDragOver={event => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
        }}
        onDrop={event => {
          event.preventDefault();
          addFiles(Array.from(event.dataTransfer.files));
        }}
        className="mt-1 flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-5 py-4 text-center hover:border-accent-500 hover:bg-accent-50"
      >
        <span className="text-sm font-medium text-ink-700">
          Drop files here or choose files
        </span>
        <span className="mt-1 text-xs text-ink-500">
          {spec.accept?.join(', ') || 'PDF, documents, Markdown, presentations, spreadsheets, code, images'}
          {' · '}up to {maximum} file{maximum === 1 ? '' : 's'}
          {' · '}{formatBytes(capabilities.max_file_size_bytes)} each
        </span>
      </label>
      <input
        id={inputId}
        type="file"
        multiple={Boolean(spec.multiple)}
        accept={extensions.join(',')}
        className="sr-only"
        onChange={event => {
          addFiles(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />

      {files.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {files.map((file, index) => (
            <li
              key={`${file.name}:${file.size}`}
              className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-3 py-2"
            >
              <div className="min-w-0">
                <div className="truncate text-sm text-ink-700">{file.name}</div>
                <div className="text-xs text-ink-500">
                  {formatBytes(file.size)}
                  {file.type ? ` · ${file.type}` : ''}
                </div>
              </div>
              <button
                type="button"
                onClick={() => onChange(files.filter((_, i) => i !== index))}
                className="ml-3 text-xs text-bad hover:underline"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  );
}

export function RunDialog({
  workflowName,
  workflowYaml,
  inputs,
  onClose,
}: {
  workflowName: string;
  workflowYaml: string;
  inputs: Record<string, WorkflowInputSpec>;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, string>>({});
  const [fileValues, setFileValues] = useState<Record<string, File[]>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [capabilities, setCapabilities] = useState(FALLBACK_CAPABILITIES);

  useEffect(() => {
    api.workflowFileCapabilities()
      .then(setCapabilities)
      .catch(() => {
        // The picker remains usable with the same conservative local defaults.
      });
  }, []);

  const keys = Object.keys(inputs);

  async function launch() {
    const nextErrors: Record<string, string> = {};
    const runInputs: Record<string, unknown> = {};

    for (const key of keys) {
      const spec = inputs[key];
      if (spec.type === 'file') {
        const selected = fileValues[key] ?? [];
        if (spec.required && selected.length === 0) {
          nextErrors[key] = 'Add at least one file.';
        }
        continue;
      }

      const value = values[key] ?? '';
      if (spec.required && !value.trim()) {
        nextErrors[key] = 'This input is required.';
        continue;
      }
      if (spec.type === 'json' && value.trim()) {
        try {
          runInputs[key] = JSON.parse(value);
        } catch {
          nextErrors[key] = 'Enter valid JSON.';
        }
      } else if (value || spec.required) {
        runInputs[key] = value;
      }
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }

    setUploading(true);
    setLaunchError(null);
    try {
      for (const key of keys) {
        const spec = inputs[key];
        if (spec.type !== 'file') continue;
        const selected = fileValues[key] ?? [];
        if (selected.length === 0) continue;
        const uploaded = await api.uploadWorkflowFiles(selected);
        runInputs[key] = spec.multiple
          ? uploaded.files
          : uploaded.files[0];
      }

      const runId = crypto.randomUUID();
      navigate(`/cockpit/${runId}`, {
        state: { workflowYaml, workflowName, inputs: runInputs },
      });
    } catch (error: any) {
      setLaunchError(String(error.message ?? error));
      setUploading(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Run {workflowName}</h2>
            <p className="text-xs text-ink-500 mt-0.5">
              Add files and provide the workflow&apos;s other inputs.
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={uploading}
            className="text-ink-500 hover:text-ink-900 text-xl leading-none disabled:opacity-50"
          >
            ×
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {keys.length === 0 && (
            <div className="text-sm text-ink-500">
              This workflow declares no inputs.
            </div>
          )}
          {keys.map((key, index) => {
            const spec = inputs[key];
            if (spec.type === 'file') {
              return (
                <FileInputField
                  key={key}
                  inputId={`workflow-file-${index}`}
                  inputName={key}
                  spec={spec}
                  files={fileValues[key] ?? []}
                  capabilities={capabilities}
                  error={errors[key]}
                  onChange={(nextFiles, nextError) => {
                    setFileValues(current => ({
                      ...current,
                      [key]: nextFiles,
                    }));
                    setErrors(current => {
                      const next = { ...current };
                      if (nextError) next[key] = nextError;
                      else delete next[key];
                      return next;
                    });
                  }}
                />
              );
            }

            return (
              <div key={key}>
                <label className="block text-sm font-medium text-ink-700">
                  {key}
                  {spec.required && <span className="ml-1 text-bad">*</span>}
                  <span className="ml-2 text-xs font-normal text-ink-500">
                    ({spec.type})
                  </span>
                </label>
                {spec.description && (
                  <p className="text-xs text-ink-500 mb-1">{spec.description}</p>
                )}
                <textarea
                  rows={spec.type === 'json' ? 6 : 3}
                  value={values[key] ?? ''}
                  onChange={event => {
                    setValues(current => ({
                      ...current,
                      [key]: event.target.value,
                    }));
                    setErrors(current => {
                      const next = { ...current };
                      delete next[key];
                      return next;
                    });
                  }}
                  placeholder={
                    spec.type === 'json'
                      ? '{"key": "value"}'
                      : `Enter ${key}…`
                  }
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border font-mono"
                />
                {errors[key] && (
                  <p className="mt-1 text-xs text-bad">{errors[key]}</p>
                )}
              </div>
            );
          })}
          {launchError && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-bad">
              {launchError}
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-slate-200 flex items-center justify-between gap-3">
          <span className="text-xs text-ink-500">
            Files are stored once and reused by token-saving workflow retries.
          </span>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              disabled={uploading}
              className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={launch}
              disabled={uploading}
              className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
            >
              {uploading ? 'Uploading files…' : 'Run workflow'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
