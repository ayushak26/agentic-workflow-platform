export function Spinner({ label = 'Loading…' }: { label?: string }) {
    return (
      <div className="flex items-center gap-2 text-ink-500 text-sm">
        <span className="inline-block h-3 w-3 rounded-full border-2 border-accent-600 border-t-transparent animate-spin" />
        {label}
      </div>
    );
  }