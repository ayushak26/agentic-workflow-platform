// Small, dependency-free icon set — no icon font or external SVG library is
// loaded anywhere in this app, so every icon is drawn inline from basic
// shapes rather than referencing glyphs that would silently fail to render.
export type IconName =
  | 'layout'
  | 'save'
  | 'topology'
  | 'flask'
  | 'terminal'
  | 'cloud'
  | 'checklist'
  | 'settings'
  | 'bell'
  | 'coin'
  | 'menu'
  | 'chevron-left'
  | 'chevron-right'
  | 'history'
  | 'check'
  | 'play'
  | 'undo'
  | 'redo'
  | 'star'
  | 'star-filled'
  | 'search'
  | 'filter'
  | 'grid'
  | 'rows'
  | 'refresh'
  | 'upload'
  | 'trash'
  | 'more-vertical'
  | 'image'
  | 'expand'
  | 'collapse'
  | 'columns'
  | 'flow-vertical'
  | 'flow-horizontal'
  | 'note';

function IconPath({ name }: { name: IconName }) {
  switch (name) {
    case 'layout':
      return (
        <>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <line x1="9" y1="3" x2="9" y2="21" />
        </>
      );
    case 'save':
      return (
        <>
          <path d="M5 3h11l3 3v15H5z" />
          <path d="M8 3v6h8V3" />
          <path d="M7 21v-7h10v7" />
        </>
      );
    case 'topology':
      return (
        <>
          <circle cx="12" cy="5" r="2.4" />
          <circle cx="5" cy="18" r="2.4" />
          <circle cx="19" cy="18" r="2.4" />
          <path d="M10.4 6.8 6.6 15.8" />
          <path d="M13.6 6.8 17.4 15.8" />
        </>
      );
    case 'flask':
      return (
        <>
          <path d="M9 2h6" />
          <path d="M10 2v6.5L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 8.5V2" />
          <path d="M7.5 15h9" />
        </>
      );
    case 'terminal':
      return (
        <>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <polyline points="7,9 10.5,12 7,15" />
          <line x1="12" y1="15" x2="17" y2="15" />
        </>
      );
    case 'cloud':
      return (
        <path d="M7 18a4 4 0 0 1-.4-8 5 5 0 0 1 9.6-1.6A4.5 4.5 0 0 1 17 18z" />
      );
    case 'checklist':
      return (
        <>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <polyline points="7,9 8.5,10.5 11,7.5" />
          <line x1="13" y1="9" x2="17.5" y2="9" />
          <polyline points="7,15 8.5,16.5 11,13.5" />
          <line x1="13" y1="15" x2="17.5" y2="15" />
        </>
      );
    case 'settings':
      return (
        <>
          <circle cx="12" cy="12" r="3.2" />
          <circle cx="12" cy="12" r="8" strokeDasharray="2.6 3.2" />
        </>
      );
    case 'bell':
      return (
        <>
          <path d="M6 10a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 14 6 10Z" />
          <path d="M10 19a2 2 0 0 0 4 0" />
        </>
      );
    case 'coin':
      return (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 7.5v9" />
          <path d="M14.8 9.6a2.6 2.6 0 0 0-2.4-1.4c-1.5 0-2.6.9-2.6 2s1 1.7 2.6 2 2.6.8 2.6 2-1.1 2-2.6 2a2.7 2.7 0 0 1-2.5-1.4" />
        </>
      );
    case 'menu':
      return (
        <>
          <line x1="4" y1="7" x2="20" y2="7" />
          <line x1="4" y1="12" x2="20" y2="12" />
          <line x1="4" y1="17" x2="20" y2="17" />
        </>
      );
    case 'chevron-left':
      return <polyline points="14.5,5 8,12 14.5,19" />;
    case 'chevron-right':
      return <polyline points="9.5,5 16,12 9.5,19" />;
    case 'history':
      return (
        <>
          <circle cx="12" cy="13" r="8" />
          <polyline points="12,9 12,13 15,15" />
          <path d="M4.5 6.5 3 4v3.4h3.4" />
        </>
      );
    case 'check':
      return <polyline points="4.5,12.5 9.5,17.5 19.5,6.5" />;
    case 'play':
      return <path d="M6.5 4.5v15l13-7.5z" />;
    case 'undo':
      return (
        <>
          <path d="M6 8H15a5.5 5.5 0 0 1 0 11h-3" />
          <polyline points="9.5,4 6,8 9.5,12" />
        </>
      );
    case 'redo':
      return (
        <>
          <path d="M18 8H9a5.5 5.5 0 0 0 0 11h3" />
          <polyline points="14.5,4 18,8 14.5,12" />
        </>
      );
    case 'star':
      return <path d="M12 4 14.4 9.6 20.5 10.2 15.9 14.1 17.3 20 12 16.7 6.7 20 8.1 14.1 3.5 10.2 9.6 9.6Z" />;
    case 'star-filled':
      return <path d="M12 4 14.4 9.6 20.5 10.2 15.9 14.1 17.3 20 12 16.7 6.7 20 8.1 14.1 3.5 10.2 9.6 9.6Z" fill="currentColor" />;
    case 'search':
      return (
        <>
          <circle cx="10.5" cy="10.5" r="6.5" />
          <line x1="15.3" y1="15.3" x2="20.5" y2="20.5" />
        </>
      );
    case 'note':
      return (
        <>
          <path d="M4 4h12l4 4v12H4Z" />
          <path d="M16 4v4h4" />
        </>
      );
    case 'filter':
      return <path d="M4 5h16l-6 7v6l-4 2v-8z" />;
    case 'grid':
      return (
        <>
          <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
          <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
          <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
          <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
        </>
      );
    case 'rows':
      return (
        <>
          <rect x="3.5" y="4.5" width="17" height="4.5" rx="1" />
          <rect x="3.5" y="10.75" width="17" height="4.5" rx="1" />
          <rect x="3.5" y="17" width="17" height="4.5" rx="1" />
        </>
      );
    case 'refresh':
      return (
        <>
          <path d="M4.5 12a7.5 7.5 0 0 1 12.6-5.5L19.5 8" />
          <polyline points="19.5,4 19.5,8 15.5,8" />
          <path d="M19.5 12a7.5 7.5 0 0 1-12.6 5.5L4.5 16" />
          <polyline points="4.5,20 4.5,16 8.5,16" />
        </>
      );
    case 'upload':
      return (
        <>
          <path d="M12 16V5" />
          <polyline points="7.5,9.5 12,5 16.5,9.5" />
          <path d="M4.5 16v3a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3" />
        </>
      );
    case 'trash':
      return (
        <>
          <path d="M4.5 7h15" />
          <path d="M9.5 7V4.5h5V7" />
          <path d="M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h6a1.5 1.5 0 0 0 1.5-1.5L17.5 7" />
          <path d="M10.2 11v6" />
          <path d="M13.8 11v6" />
        </>
      );
    case 'more-vertical':
      return (
        <>
          <circle cx="12" cy="5.5" r="1.3" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" />
          <circle cx="12" cy="18.5" r="1.3" fill="currentColor" stroke="none" />
        </>
      );
    case 'image':
      return (
        <>
          <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
          <circle cx="8.75" cy="9.75" r="1.4" />
          <path d="M4 17.5l4.6-4.6 3.4 3.4 2.6-2.6 5.4 5.2" />
        </>
      );
    case 'expand':
      return (
        <>
          <polyline points="4,9.5 4,4 9.5,4" />
          <polyline points="14.5,4 20,4 20,9.5" />
          <polyline points="20,14.5 20,20 14.5,20" />
          <polyline points="9.5,20 4,20 4,14.5" />
        </>
      );
    case 'collapse':
      return (
        <>
          <polyline points="9.5,4 9.5,9.5 4,9.5" />
          <polyline points="14.5,4 14.5,9.5 20,9.5" />
          <polyline points="20,14.5 14.5,14.5 14.5,20" />
          <polyline points="4,14.5 9.5,14.5 9.5,20" />
        </>
      );
    case 'columns':
      return (
        <>
          <rect x="3.5" y="4.5" width="5" height="15" rx="1.2" />
          <rect x="10.5" y="4.5" width="5" height="15" rx="1.2" />
          <rect x="17.5" y="4.5" width="3" height="15" rx="1.2" />
        </>
      );
    case 'flow-vertical':
      return (
        <>
          <rect x="8" y="3.5" width="8" height="5" rx="1.2" />
          <rect x="8" y="15.5" width="8" height="5" rx="1.2" />
          <path d="M12 8.5v7" />
          <polyline points="9.8,13.2 12,15.5 14.2,13.2" />
        </>
      );
    case 'flow-horizontal':
      return (
        <>
          <rect x="3.5" y="8" width="5" height="8" rx="1.2" />
          <rect x="15.5" y="8" width="5" height="8" rx="1.2" />
          <path d="M8.5 12h7" />
          <polyline points="13.2,9.8 15.5,12 13.2,14.2" />
        </>
      );
    default:
      return null;
  }
}

export function Icon({
  name,
  size = 16,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <IconPath name={name} />
    </svg>
  );
}
