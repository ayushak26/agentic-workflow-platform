import { createContext, useContext } from 'react';
import type { CollectionResource } from '../../api/knowledge';

/**
 * The one selected Collection, shared by every Knowledge Studio tab.
 *
 * Each page used to hold its own collectionId and silently default to the
 * first collection, so ingesting into one collection and then opening
 * Documents or the Playground could show a different one with nothing on
 * screen saying so.
 *
 * Context plumbing lives here rather than beside the components so the
 * component module only exports components (react-refresh).
 */

export const COLLECTION_STORAGE_KEY = 'eurskem.knowledge.collection';

export type CollectionContextValue = {
  collections: CollectionResource[];
  collectionId: string;
  collection: CollectionResource | null;
  setCollectionId: (id: string) => void;
  refresh: () => Promise<void>;
  loading: boolean;
  error: string | null;
};

export const CollectionContext = createContext<CollectionContextValue | null>(null);

export function useCollection(): CollectionContextValue {
  const value = useContext(CollectionContext);
  if (!value) throw new Error('useCollection must be used inside <CollectionProvider>');
  return value;
}
