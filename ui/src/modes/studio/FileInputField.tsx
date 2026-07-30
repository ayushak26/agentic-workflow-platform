import { useMemo } from 'react';

import type { WorkflowFileCapabilities, WorkflowFileReference } from '../../api/types';
import { acceptedExtensions, extensionOf, formatBytes } from './fileInputUtils';
import type { WorkflowInputSpec } from './yaml-bridge';

export function FileInputField({
  inputId,
  inputName,
  spec,
  files,
  loadedRefs,
  onClearLoaded,
  capabilities,
  error,
  onChange,
}: {
  inputId: string;
  inputName: string;
  spec: WorkflowInputSpec;
  files: File[];
  loadedRefs: WorkflowFileReference[];
  onClearLoaded: () => void;
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

      {loadedRefs.length > 0 ? (
        <div className="mt-1 rounded-lg border border-accent-200 bg-accent-50 px-3 py-2.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-accent-700">
              Loaded from JSON
            </span>
            <button
              type="button"
              onClick={onClearLoaded}
              className="text-xs text-ink-500 hover:underline"
            >
              Clear · upload manually instead
            </button>
          </div>
          <ul className="mt-1.5 space-y-1">
            {loadedRefs.map(ref => (
              <li key={ref.file_id} className="text-xs text-ink-700 truncate">
                {ref.name} <span className="text-ink-500">({formatBytes(ref.size_bytes)})</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <>
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
        </>
      )}
      {error && <p className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  );
}
