// Shared wordmark lockup used on the splash screen and in the sidebar.
// There's no shipped logo asset in ui/public — this draws the mark from
// CSS/text instead of pointing an <img> at a file that doesn't exist,
// which would otherwise render as a broken-image icon on first paint.
export function BrandMark({ size = 'md' }: { size?: 'sm' | 'md' }) {
  const badge = size === 'md' ? 48 : 32;
  return (
    <div className="flex items-center gap-2.5">
      <div
        className="grid flex-none place-items-center rounded-xl font-bold text-white"
        style={{
          width: badge,
          height: badge,
          background: 'var(--brand-teal-600)',
          fontSize: size === 'md' ? 22 : 15,
        }}
      >
        E
      </div>
      {size === 'md' && (
        <div className="text-left">
          <div className="text-lg font-semibold text-white">
            Eurskem <span style={{ color: 'var(--brand-teal-400)' }}>AI</span>
          </div>
        </div>
      )}
    </div>
  );
}
