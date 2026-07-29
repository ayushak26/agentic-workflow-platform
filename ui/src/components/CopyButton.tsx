import { useState, type MouseEvent } from 'react';

export function CopyButton({
  text,
  label = 'Copy',
  copiedLabel = 'Copied',
  className = '',
}: {
  text: string;
  label?: string;
  copiedLabel?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy(e: MouseEvent) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={
        'rounded border border-slate-300 bg-white px-2 py-1 text-[10px] '
        + `text-ink-700 hover:bg-slate-100 ${className}`
      }
    >
      {copied ? copiedLabel : label}
    </button>
  );
}
