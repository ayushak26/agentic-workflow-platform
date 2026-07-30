import type { WorkflowFileCapabilities, WorkflowFileReference } from '../../api/types';
import type { WorkflowInputSpec } from './yaml-bridge';

export function isFileReferenceLike(value: unknown): value is WorkflowFileReference {
  return (
    typeof value === 'object'
    && value !== null
    && (value as { kind?: unknown }).kind === 'workflow_file'
    && typeof (value as { file_id?: unknown }).file_id === 'string'
  );
}

export function fileReferencesFrom(value: unknown): WorkflowFileReference[] | null {
  if (isFileReferenceLike(value)) return [value];
  if (Array.isArray(value) && value.length > 0 && value.every(isFileReferenceLike)) {
    return value as WorkflowFileReference[];
  }
  return null;
}

const FALLBACK_CATEGORIES: Record<string, string[]> = {
  pdf: ['.pdf'],
  document: ['.docx', '.txt'],
  markdown: ['.md', '.markdown'],
  presentation: ['.pptx'],
  spreadsheet: ['.xlsx'],
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

export const FALLBACK_FILE_CAPABILITIES: WorkflowFileCapabilities = {
  categories: FALLBACK_CATEGORIES,
  extensions: Object.values(FALLBACK_CATEGORIES).flat(),
  extractable_extensions: [
    '.pdf', '.docx', '.pptx', '.xlsx', '.txt', '.md', '.markdown',
    '.py', '.js', '.ts', '.tsx', '.json', '.yaml', '.yml',
  ],
  reference_only_extensions: FALLBACK_CATEGORIES.image,
  max_file_size_bytes: 50 * 1024 * 1024,
  max_files_per_input: 20,
};

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function acceptedExtensions(
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
