import { useEffect, useRef } from 'react';

export type RichEditorValue = {
  text: string;
  html: string;
};

type EditorCommand = {
  label: string;
  title: string;
  command: string;
  value?: string;
};

const COMMANDS: EditorCommand[] = [
  { label: 'P', title: 'Paragraph', command: 'formatBlock', value: 'p' },
  { label: 'H1', title: 'Heading 1', command: 'formatBlock', value: 'h1' },
  { label: 'H2', title: 'Heading 2', command: 'formatBlock', value: 'h2' },
  { label: 'B', title: 'Bold', command: 'bold' },
  { label: 'I', title: 'Italic', command: 'italic' },
  { label: 'U', title: 'Underline', command: 'underline' },
  { label: '• List', title: 'Bulleted list', command: 'insertUnorderedList' },
  { label: '1. List', title: 'Numbered list', command: 'insertOrderedList' },
  { label: '❝', title: 'Quote', command: 'formatBlock', value: 'blockquote' },
];

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function plainTextToHtml(value: string): string {
  if (!value) return '<p><br></p>';
  return value
    .split(/\n{2,}/)
    .map((block) => `<p>${escapeHtml(block).replaceAll('\n', '<br>')}</p>`)
    .join('');
}

export function RichTextEditor({
  initialText,
  resetKey,
  disabled = false,
  onChange,
}: {
  initialText: string;
  resetKey: number;
  disabled?: boolean;
  onChange: (value: RichEditorValue) => void;
}) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const resetTextRef = useRef(initialText);
  resetTextRef.current = initialText;

  useEffect(() => {
    if (!editorRef.current) return;
    // Workflow/LLM text is always escaped. Raw upstream HTML is never inserted
    // into the browser, so the editor cannot become an XSS entry point.
    editorRef.current.innerHTML = plainTextToHtml(resetTextRef.current);
  }, [resetKey]);

  function emitChange() {
    const editor = editorRef.current;
    if (!editor) return;
    onChange({
      text: editor.innerText.replace(/\u00a0/g, ' '),
      html: editor.innerHTML,
    });
  }

  function runCommand(command: string, value?: string) {
    if (disabled) return;
    editorRef.current?.focus();
    document.execCommand(command, false, value);
    emitChange();
  }

  function insertPlainText(event: React.ClipboardEvent<HTMLDivElement>) {
    event.preventDefault();
    const text = event.clipboardData.getData('text/plain');
    document.execCommand('insertText', false, text);
    emitChange();
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 bg-slate-50 px-2 py-2">
        {COMMANDS.map((item) => (
          <button
            key={`${item.command}-${item.value ?? ''}`}
            type="button"
            title={item.title}
            disabled={disabled}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => runCommand(item.command, item.value)}
            className="min-w-8 rounded-md border border-transparent px-2 py-1 text-xs font-medium text-ink-700 hover:border-slate-300 hover:bg-white disabled:opacity-40"
          >
            {item.label}
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-slate-300" />
        <button
          type="button"
          title="Undo"
          disabled={disabled}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => runCommand('undo')}
          className="rounded-md px-2 py-1 text-xs text-ink-700 hover:bg-white disabled:opacity-40"
        >
          Undo
        </button>
        <button
          type="button"
          title="Redo"
          disabled={disabled}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => runCommand('redo')}
          className="rounded-md px-2 py-1 text-xs text-ink-700 hover:bg-white disabled:opacity-40"
        >
          Redo
        </button>
        <button
          type="button"
          title="Clear formatting"
          disabled={disabled}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => runCommand('removeFormat')}
          className="rounded-md px-2 py-1 text-xs text-ink-700 hover:bg-white disabled:opacity-40"
        >
          Clear
        </button>
      </div>

      <div
        ref={editorRef}
        role="textbox"
        aria-label="Human review editor"
        aria-multiline="true"
        contentEditable={!disabled}
        suppressContentEditableWarning
        spellCheck
        onInput={emitChange}
        onBlur={emitChange}
        onPaste={insertPlainText}
        className="hitl-rich-editor min-h-[420px] max-h-[62vh] overflow-y-auto px-7 py-6 text-[15px] leading-7 text-ink-900 outline-none"
      />
    </div>
  );
}
