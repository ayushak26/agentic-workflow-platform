import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// vite.config.ts runs vitest without `globals: true`, so React Testing
// Library's auto-cleanup (which relies on detecting a global `afterEach`)
// never registers on its own — without this, every render() in a test file
// piles onto the same document and later tests see duplicate/stale DOM.
afterEach(() => {
  cleanup();
});
