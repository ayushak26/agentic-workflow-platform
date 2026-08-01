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
  | 'chevron-right';

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
