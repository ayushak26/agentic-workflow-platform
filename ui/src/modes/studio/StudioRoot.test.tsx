import { act, render, screen, waitFor } from '@testing-library/react';
import { lazy } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./Library', () => ({ Library: () => <div data-testid="workflows-screen" /> }));
vi.mock('./Builder', () => ({ Builder: () => <div data-testid="builder-screen" /> }));
vi.mock('./Cockpit', () => ({ Cockpit: () => <div data-testid="cockpit-screen" /> }));
vi.mock('./business-chat/BusinessChat', () => ({
  BusinessChat: () => <div data-testid="chat-screen" />,
}));
vi.mock('./RunHistory', () => ({
  RunHistory: () => <div data-testid="run-history-screen" />,
}));
vi.mock('./RunCandidates', () => ({
  RunCandidates: () => <div data-testid="candidates-screen" />,
}));
vi.mock('./ProposalReview', () => ({
  ProposalReview: () => <div data-testid="proposal-review-screen" />,
}));

import { StudioRoot } from './StudioRoot';
import { StudioLayout } from './StudioLayout';

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StudioRoot />
      <LocationProbe />
    </MemoryRouter>,
  );
}

async function expectRedirect(from: string, to: string, screenTestId: string) {
  renderRoute(from);
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(to));
  expect(screen.getByTestId(screenTestId)).toBeInTheDocument();
}

describe('StudioRoot canonical workflow routes', () => {
  it('keeps Studio navigation mounted while a lazy screen loads', async () => {
    let resolveScreen: (() => void) | undefined;
    const DeferredScreen = lazy(() => new Promise<{ default: () => React.ReactNode }>(resolve => {
      resolveScreen = () => resolve({ default: () => <div data-testid="deferred-screen" /> });
    }));

    render(
      <MemoryRouter initialEntries={['/deferred']}>
        <Routes>
          <Route element={<StudioLayout />}>
            <Route path="deferred" element={<DeferredScreen />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: 'Chat' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Loading screen…');

    await act(async () => { resolveScreen?.(); });
    expect(await screen.findByTestId('deferred-screen')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Chat' })).toBeInTheDocument();
  });

  it('shows canonical navigation without Business View or Pipeline entries', () => {
    renderRoute('/chat');

    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat');
    expect(screen.getByRole('link', { name: 'Workflows' })).toHaveAttribute('href', '/workflows');
    expect(screen.getByRole('link', { name: 'Builder' })).toHaveAttribute('href', '/builder');
    expect(screen.getByRole('link', { name: 'Workflow runs' })).toHaveAttribute('href', '/workflow-runs');
    expect(screen.queryByRole('link', { name: /business view/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /pipeline/i })).not.toBeInTheDocument();
  });

  it('redirects the Studio index to Chat', async () => {
    await expectRedirect('/', '/chat', 'chat-screen');
  });

  it('redirects legacy Library to Workflows', async () => {
    await expectRedirect('/library', '/workflows', 'workflows-screen');
  });

  it.each([
    ['/history', '/workflow-runs'],
    ['/history/run-123', '/workflow-runs/run-123'],
    ['/guided/run-456', '/workflow-runs/run-456'],
    ['/business-chat/private/legacy-workflow', '/chat'],
  ])('redirects legacy path %s to %s', async (from, to) => {
    await expectRedirect(from, to, to === '/chat' ? 'chat-screen' : 'run-history-screen');
  });

  it.each(['/business/run-123', '/pipelines'])('does not resolve removed path %s', path => {
    renderRoute(path);

    expect(screen.getByTestId('location')).toHaveTextContent(path);
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByTestId('run-history-screen')).not.toBeInTheDocument();
    expect(screen.queryByTestId('workflows-screen')).not.toBeInTheDocument();
  });
});