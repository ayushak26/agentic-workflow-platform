type JsonPathPart = string | number;

export type PlainTextJsonAdapter = {
  displayText: string;
  serialize: (editedText: string) => string;
};

const PREFERRED_TEXT_KEYS = [
  'text',
  'message',
  'answer',
  'draft',
  'content',
  'summary',
  'response',
  'description',
];

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function valueAtPath(value: unknown, path: JsonPathPart[]): unknown {
  let current = value;
  for (const part of path) {
    if (current == null || typeof current !== 'object') return undefined;
    current = (current as Record<JsonPathPart, unknown>)[part];
  }
  return current;
}

function replaceAtPath(value: unknown, path: JsonPathPart[], replacement: string): unknown {
  if (path.length === 0) return replacement;
  const copy = cloneJson(value);
  let current = copy as Record<JsonPathPart, unknown>;
  for (const part of path.slice(0, -1)) {
    current = current[part] as Record<JsonPathPart, unknown>;
  }
  current[path[path.length - 1]] = replacement;
  return copy;
}

function stringPaths(value: unknown, path: JsonPathPart[] = []): JsonPathPart[][] {
  if (typeof value === 'string') return [path];
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => stringPaths(item, [...path, index]));
  }
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, item]) => stringPaths(item, [...path, key]));
}

function preferredStringPath(value: unknown): JsonPathPart[] | null {
  const paths = stringPaths(value);
  if (paths.length === 0) return null;
  for (const preferredKey of PREFERRED_TEXT_KEYS) {
    const match = paths.find(path => (
      String(path[path.length - 1]).toLowerCase() === preferredKey
    ));
    if (match) return match;
  }
  return paths.length === 1 ? paths[0] : null;
}

function readableText(value: unknown, prefix = ''): string[] {
  if (value == null) return [];
  if (typeof value !== 'object') {
    return [`${prefix}${String(value)}`];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => readableText(item, `${index + 1}. `));
  }
  return Object.entries(value).flatMap(([key, item]) => {
    const label = key.replaceAll('_', ' ');
    if (item != null && typeof item === 'object') {
      const nested = readableText(item);
      return nested.length > 0 ? [`${label}:`, ...nested.map(line => `  ${line}`)] : [];
    }
    return readableText(item, `${label}: `);
  });
}

/**
 * Adapts a JSON review document to a plain-text Business Chat editor.
 *
 * A clear text field is edited in place so the original structured object is
 * preserved. For genuinely ambiguous structures, the reviewer still sees
 * readable text and their edit is encoded as a JSON string; the backend can
 * therefore continue using its existing JSON restoration path without asking
 * the person to write JSON syntax.
 */
export function plainTextJsonAdapter(serialized: string): PlainTextJsonAdapter {
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return { displayText: serialized, serialize: editedText => JSON.stringify(editedText) };
  }

  if (typeof parsed === 'string') {
    return { displayText: parsed, serialize: editedText => JSON.stringify(editedText) };
  }

  const path = preferredStringPath(parsed);
  if (path) {
    const displayText = valueAtPath(parsed, path);
    return {
      displayText: typeof displayText === 'string' ? displayText : '',
      serialize: editedText => JSON.stringify(replaceAtPath(parsed, path, editedText)),
    };
  }

  return {
    displayText: readableText(parsed).join('\n'),
    serialize: editedText => JSON.stringify(editedText),
  };
}