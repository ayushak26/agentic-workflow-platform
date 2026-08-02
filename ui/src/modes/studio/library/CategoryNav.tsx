import { LIBRARY_CATEGORIES, type LibraryCategoryId } from './categories';

export type LibraryNavSelection = LibraryCategoryId | 'all' | 'favorites';

export function CategoryNav({
  counts,
  favoritesCount,
  totalCount,
  selection,
  onSelect,
  open,
}: {
  counts: Partial<Record<LibraryCategoryId, number>>;
  favoritesCount: number;
  totalCount: number;
  selection: LibraryNavSelection;
  onSelect: (selection: LibraryNavSelection) => void;
  // Only meaningful below the 900px breakpoint, where this nav becomes an
  // overlay drawer instead of a permanent sidebar.
  open?: boolean;
}) {
  return (
    <nav className={`library-category-nav ${open ? 'is-open' : ''}`} aria-label="Browse by purpose">
      <button
        type="button"
        className={selection === 'all' ? 'is-active' : ''}
        aria-current={selection === 'all' ? 'page' : undefined}
        onClick={() => onSelect('all')}
      >
        <span>All workflows</span>
        <span className="library-category-count">{totalCount}</span>
      </button>
      <button
        type="button"
        className={selection === 'favorites' ? 'is-active' : ''}
        aria-current={selection === 'favorites' ? 'page' : undefined}
        onClick={() => onSelect('favorites')}
      >
        <span>Favorites</span>
        <span className="library-category-count">{favoritesCount}</span>
      </button>

      <div className="library-category-divider" role="separator" />

      {LIBRARY_CATEGORIES.map(category => {
        const count = counts[category.id] ?? 0;
        if (count === 0 && category.id !== 'custom-workflows') return null;
        return (
          <button
            key={category.id}
            type="button"
            className={selection === category.id ? 'is-active' : ''}
            aria-current={selection === category.id ? 'page' : undefined}
            onClick={() => onSelect(category.id)}
          >
            <span>{category.label}</span>
            <span className="library-category-count">{count}</span>
          </button>
        );
      })}
    </nav>
  );
}
