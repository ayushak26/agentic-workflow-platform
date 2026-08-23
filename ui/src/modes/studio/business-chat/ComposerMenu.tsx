import { useEffect, useRef } from 'react';

export type ComposerMenuItem = {
  id: string;
  label: string;
  description: string;
  icon?: string;
};

export function ComposerMenu({
  label,
  items,
  onChoose,
  onClose,
}: {
  label: string;
  items: ComposerMenuItem[];
  onChoose: (item: ComposerMenuItem) => void;
  onClose: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) onClose();
    };
    document.addEventListener('mousedown', closeOutside);
    return () => {
      document.removeEventListener('mousedown', closeOutside);
      restoreFocusRef.current?.focus();
    };
  }, [onClose]);

  function moveFocus(direction: 1 | -1) {
    const buttons = [...(rootRef.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])];
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    buttons[(current + direction + buttons.length) % buttons.length]?.focus();
  }

  return (
    <div
      ref={rootRef}
      className="chat-composer-menu"
      role="menu"
      aria-label={label}
      onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); onClose(); }
        if (event.key === 'ArrowDown') { event.preventDefault(); moveFocus(1); }
        if (event.key === 'ArrowUp') { event.preventDefault(); moveFocus(-1); }
      }}
    >
      {items.map(item => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          className={item.icon ? 'has-icon' : 'without-icon'}
          onClick={() => onChoose(item)}
        >
          {item.icon && <span className="chat-composer-menu-icon" aria-hidden>{item.icon}</span>}
          <span className="chat-composer-menu-content"><strong>{item.label}</strong><small>{item.description}</small></span>
        </button>
      ))}
    </div>
  );
}