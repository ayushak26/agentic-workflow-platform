import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, rehydrate } from '../../../api/client';
import type { RunDetail } from '../../../api/types';
import { useRunHistoryData } from './useRunHistoryData';

vi.mock('../../../api/client', () => ({
  api: { runHistory: vi.fn(), runDetail: vi.fn() },
  rehydrate: vi.fn(),
}));

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

describe('useRunHistoryData polling', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for the current list request before scheduling the next poll', async () => {
    let resolveFirst: ((value: { count: number; runs: [] }) => void) | undefined;
    vi.mocked(api.runHistory)
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }))
      .mockResolvedValue({ count: 0, runs: [] });

    renderHook(() => useRunHistoryData(undefined), { wrapper });
    expect(api.runHistory).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(7500));
    expect(api.runHistory).toHaveBeenCalledTimes(1);

    await act(async () => { resolveFirst?.({ count: 0, runs: [] }); });
    await waitFor(() => expect(resolveFirst).toBeDefined());
    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(api.runHistory).toHaveBeenCalledTimes(2);
  });

  it('does not schedule another list poll when the request settles after unmount', async () => {
    let resolveFirst: ((value: { count: number; runs: [] }) => void) | undefined;
    vi.mocked(api.runHistory).mockImplementationOnce(() => new Promise(resolve => {
      resolveFirst = resolve;
    }));

    const { unmount } = renderHook(() => useRunHistoryData(undefined), { wrapper });
    expect(api.runHistory).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => { resolveFirst?.({ count: 0, runs: [] }); });
    await act(() => vi.advanceTimersByTimeAsync(5000));

    expect(api.runHistory).toHaveBeenCalledTimes(1);
  });

  it('stops list polling when session recovery fails', async () => {
    vi.mocked(api.runHistory).mockRejectedValue(new Error('401 Not authenticated'));
    vi.mocked(rehydrate).mockResolvedValue(null);

    const { result } = renderHook(() => useRunHistoryData(undefined), { wrapper });
    await waitFor(() => expect(result.current.listErr).toBe('Session expired — please log in again.'));
    await act(() => vi.advanceTimersByTimeAsync(7500));

    expect(rehydrate).toHaveBeenCalledTimes(1);
    expect(api.runHistory).toHaveBeenCalledTimes(1);
  });

  it('continues list polling after successful session recovery', async () => {
    vi.mocked(api.runHistory)
      .mockRejectedValueOnce(new Error('401 Not authenticated'))
      .mockResolvedValue({ count: 0, runs: [] });
    vi.mocked(rehydrate).mockResolvedValue({ username: 'alice' });

    renderHook(() => useRunHistoryData(undefined), { wrapper });
    await waitFor(() => expect(rehydrate).toHaveBeenCalledTimes(1));
    await act(() => vi.advanceTimersByTimeAsync(2500));

    expect(api.runHistory).toHaveBeenCalledTimes(2);
  });

  it('stops detail polling once the selected run is terminal', async () => {
    const completedRun: RunDetail = {
      run_id: 'run-1',
      session_id: 'session-1',
      workflow_name: 'Workflow',
      status: 'completed',
      started_at: 1,
      ended_at: 2,
      duration_s: 1,
      node_count: 0,
      completed_node_count: 0,
      active_nodes: [],
      error: null,
      created_at: '2026-08-23T00:00:00Z',
      updated_at: '2026-08-23T00:00:01Z',
      inputs: {},
      outputs: {},
      node_runs: {},
    };
    vi.mocked(api.runHistory).mockResolvedValue({ count: 0, runs: [] });
    vi.mocked(api.runDetail).mockResolvedValue({
      run: completedRun,
      audit: [],
    });

    renderHook(() => useRunHistoryData('run-1'), { wrapper });
    await waitFor(() => expect(api.runDetail).toHaveBeenCalledTimes(1));
    await act(() => vi.advanceTimersByTimeAsync(6000));

    expect(api.runDetail).toHaveBeenCalledTimes(1);
  });
});