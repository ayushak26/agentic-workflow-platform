import { useEffect, useRef, useState } from 'react';

import type { OutputContract } from '../../../api/types';
import { resolveBinding, stepLabelFor } from './binding';
import { humanizeIdentifier } from '../guided/runtime-model';
import { ValuePicker } from './FieldPicker';

/**
 * A free-text field (a prompt, an email body, a document template) that can
 * embed references to earlier steps' data — shown as chips, not raw
 * `{{node.field}}` syntax, while still serializing to exactly that string
 * underneath.
 *
 * This is an *uncontrolled* contentEditable region driven by an imperative
 * controller, the same pattern used to wrap CodeMirror/Monaco in React:
 * React renders the container once, and only rewrites its DOM when `value`
 * changes for a reason other than this field's own last emission. Rewriting
 * on every keystroke (the naive "controlled contentEditable") is what causes
 * the caret to jump mid-typing — this avoids that by only ever syncing
 * outward-in when the change genuinely came from outside.
 */

const TOKEN_RE = /\{\{\s*[\w.]+\s*\??\s*\}\}/g;

function resolveChipLabel(reference: string, contract: OutputContract | null) {
  const binding = resolveBinding(reference, contract);
  if (binding.kind === 'resolved') {
    const leaf = binding.field.path.split('.').slice(-1)[0] || binding.field.path;
    return { label: humanizeIdentifier(leaf) || leaf, stepLabel: stepLabelFor(binding), resolved: true };
  }
  const inner = reference.replace(/^\{\{\s*/, '').replace(/\s*\??\s*\}\}$/, '');
  return { label: inner, stepLabel: 'Unknown reference', resolved: false };
}

function buildChip(reference: string, contract: OutputContract | null): HTMLSpanElement {
  const { label, stepLabel, resolved } = resolveChipLabel(reference, contract);
  const span = document.createElement('span');
  span.contentEditable = 'false';
  span.dataset.token = reference;
  span.setAttribute('aria-label', `${label}, from ${stepLabel}`);
  span.title = reference;
  span.className = resolved
    ? 'mx-0.5 inline-flex items-center rounded-full bg-accent-100 px-1.5 py-0 text-[11px] font-medium text-accent-800 align-baseline'
    : 'mx-0.5 inline-flex items-center rounded-full bg-red-100 px-1.5 py-0 text-[11px] font-medium text-red-700 align-baseline';
  span.textContent = resolved ? `${label} · ${stepLabel}` : `⚠ ${label}`;
  return span;
}

function deserialize(value: string, contract: OutputContract | null): DocumentFragment {
  const fragment = document.createDocumentFragment();
  let lastIndex = 0;
  const appendText = (text: string) => {
    const lines = text.split('\n');
    lines.forEach((line, index) => {
      if (line) fragment.appendChild(document.createTextNode(line));
      if (index < lines.length - 1) fragment.appendChild(document.createElement('br'));
    });
  };

  TOKEN_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = TOKEN_RE.exec(value))) {
    appendText(value.slice(lastIndex, match.index));
    fragment.appendChild(buildChip(match[0], contract));
    lastIndex = match.index + match[0].length;
  }
  appendText(value.slice(lastIndex));

  if (!fragment.hasChildNodes()) fragment.appendChild(document.createTextNode(''));
  return fragment;
}

function serialize(root: HTMLElement): string {
  let out = '';
  for (const node of Array.from(root.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? '';
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const element = node as HTMLElement;
      if (element.dataset.token) out += element.dataset.token;
      else if (element.tagName === 'BR') out += '\n';
      else if (element.tagName === 'DIV') out += `\n${serialize(element)}`;
      else out += element.textContent ?? '';
    }
  }
  return out;
}

// Caret position expressed as an offset into the *serialized* string, so the
// same "chip = its full reference length" unit is used to save and restore
// it — the on-screen chip is much shorter than its reference, but that's
// fine, this is purely an internal bookkeeping coordinate.
function getCaretOffset(root: HTMLElement): number | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;
  const anchor = selection.getRangeAt(0);
  if (!root.contains(anchor.endContainer)) return null;
  const range = document.createRange();
  range.selectNodeContents(root);
  range.setEnd(anchor.endContainer, anchor.endOffset);
  const wrapper = document.createElement('div');
  wrapper.appendChild(range.cloneContents());
  return serialize(wrapper).length;
}

function setCaretOffset(root: HTMLElement, offset: number): void {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  let remaining = offset;
  let placed = false;

  const visit = (node: ChildNode) => {
    if (placed) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const length = node.textContent?.length ?? 0;
      if (remaining <= length) {
        range.setStart(node, Math.max(0, remaining));
        placed = true;
      } else {
        remaining -= length;
      }
      return;
    }
    if (node.nodeType === Node.ELEMENT_NODE) {
      const element = node as HTMLElement;
      const unit = element.dataset.token ? element.dataset.token.length : element.tagName === 'BR' ? 1 : 0;
      if (element.dataset.token || element.tagName === 'BR') {
        if (remaining <= unit) {
          range.setStartAfter(element);
          placed = true;
        } else {
          remaining -= unit;
        }
        return;
      }
      for (const child of Array.from(element.childNodes)) {
        visit(child);
        if (placed) return;
      }
    }
  };

  for (const child of Array.from(root.childNodes)) {
    visit(child);
    if (placed) break;
  }
  if (!placed) range.selectNodeContents(root);
  range.collapse(!placed ? false : true);
  selection.removeAllRanges();
  selection.addRange(range);
}

export function TemplateTextField({
  value,
  onChange,
  contract,
  placeholder,
  rows = 4,
  disabled,
  'aria-label': ariaLabel,
}: {
  value: string;
  onChange: (next: string) => void;
  contract: OutputContract | null;
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
  'aria-label'?: string;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const lastEmittedRef = useRef<string>(value);
  const savedRangeRef = useRef<Range | null>(null);
  const isComposingRef = useRef(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [isEmpty, setIsEmpty] = useState(value.length === 0);

  // Mount, and any *external* change to `value` (e.g. the parent resets this
  // field, or another tab writes to the same config key) — never our own
  // last emission, which would otherwise bounce the caret on every keystroke.
  useEffect(() => {
    const root = rootRef.current;
    if (!root || value === lastEmittedRef.current) return;
    const caret = document.activeElement === root ? getCaretOffset(root) : null;
    root.innerHTML = '';
    root.appendChild(deserialize(value, contract));
    lastEmittedRef.current = value;
    setIsEmpty(value.length === 0);
    if (caret !== null) setCaretOffset(root, caret);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // Contract can update (a field got renamed/removed upstream) without the
  // text itself changing — re-render chip labels/broken-state to match.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const caret = document.activeElement === root ? getCaretOffset(root) : null;
    root.innerHTML = '';
    root.appendChild(deserialize(lastEmittedRef.current, contract));
    if (caret !== null) setCaretOffset(root, caret);
  }, [contract]);

  const emitChange = () => {
    const root = rootRef.current;
    if (!root) return;
    const next = serialize(root);
    setIsEmpty(root.childNodes.length === 0 || (root.childNodes.length === 1 && next === ''));
    if (next !== lastEmittedRef.current) {
      lastEmittedRef.current = next;
      onChange(next);
    }
  };

  const captureRange = () => {
    const selection = window.getSelection();
    const root = rootRef.current;
    if (!selection || !root || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    if (root.contains(range.startContainer)) savedRangeRef.current = range.cloneRange();
  };

  const insertChip = (reference: string) => {
    const root = rootRef.current;
    if (!root) return;
    root.focus();
    const range = savedRangeRef.current ?? (() => {
      const end = document.createRange();
      end.selectNodeContents(root);
      end.collapse(false);
      return end;
    })();
    range.deleteContents();
    const chip = buildChip(reference, contract);
    const spacer = document.createTextNode('');
    range.insertNode(spacer);
    range.insertNode(chip);
    range.setStartAfter(spacer);
    range.collapse(true);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    savedRangeRef.current = range.cloneRange();
    emitChange();
    setPickerOpen(false);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' || isComposingRef.current) return;
    if (rows <= 1) {
      event.preventDefault();
      return;
    }
    // Force a plain <br> rather than trusting the browser's own
    // Enter-handling, which inconsistently produces <div>-per-line in some
    // browsers — a single, predictable line-break shape keeps serialize()
    // simple and exact.
    event.preventDefault();
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const br = document.createElement('br');
    const spacer = document.createTextNode('');
    range.insertNode(spacer);
    range.insertNode(br);
    range.setStartAfter(spacer);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    emitChange();
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    const text = event.clipboardData.getData('text/plain');
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    range.deleteContents();
    // Pasted text is always literal, never scanned for {{...}} — a user
    // pasting example/documentation text with real curly braces should not
    // have it silently turned into a chip.
    const node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    emitChange();
  };

  return (
    <div>
      <div className="flex items-center justify-end">
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline disabled:opacity-50"
          disabled={disabled}
          onMouseDown={event => { event.preventDefault(); captureRange(); }}
          onClick={() => setPickerOpen(value => !value)}
          type="button"
        >
          {pickerOpen ? 'Close' : '+ Insert data'}
        </button>
      </div>
      <div className="relative mt-1">
        {isEmpty && placeholder && (
          <div className="pointer-events-none absolute left-2 top-1.5 whitespace-pre-line text-[12px] text-ink-400">
            {placeholder}
          </div>
        )}
        <div
          aria-label={ariaLabel}
          aria-multiline="true"
          className="builder-field overflow-y-auto whitespace-pre-wrap"
          contentEditable={!disabled}
          onBlur={captureRange}
          onCompositionEnd={() => { isComposingRef.current = false; emitChange(); }}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onInput={() => { if (!isComposingRef.current) emitChange(); }}
          onKeyDown={handleKeyDown}
          onKeyUp={captureRange}
          onMouseUp={captureRange}
          onPaste={handlePaste}
          ref={node => {
            rootRef.current = node;
            if (node && node.childNodes.length === 0) {
              node.appendChild(deserialize(value, contract));
            }
          }}
          role="textbox"
          style={{ minHeight: `${Math.max(1.6, rows * 1.4)}em` }}
          suppressContentEditableWarning
        />
      </div>
      {pickerOpen && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <ValuePicker
            contract={contract}
            destinationKind="text"
            onPick={field => insertChip(field.reference)}
          />
        </div>
      )}
    </div>
  );
}
