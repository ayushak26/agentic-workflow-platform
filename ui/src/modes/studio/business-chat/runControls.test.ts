import { describe, expect, it } from 'vitest';

import { attemptLabel, runControlState } from './runControls';

describe('runControlState', () => {
  it('offers pause only for an actively running attempt', () => {
    expect(runControlState({ status: 'running' })).toMatchObject({
      canPause: true, canResume: false, canRetry: false, canRestart: false,
    });
  });

  it('keeps the run active while a cooperative pause is pending', () => {
    expect(runControlState({ status: 'running', pausePending: true })).toMatchObject({
      statusLabel: 'Pause requested', canPause: false, canResume: false,
    });
  });

  it('offers generic resume only for a user-requested pause', () => {
    expect(runControlState({ status: 'paused', pauseKind: 'user_requested' })).toMatchObject({
      canResume: true, needsReview: false,
    });
    expect(runControlState({ status: 'paused', pauseKind: 'hitl_gate' })).toMatchObject({
      canResume: false, needsReview: true,
    });
  });

  it('shows subprocess pauses as an automatic wait, never a review', () => {
    expect(runControlState({ status: 'paused', pauseKind: 'subprocess' })).toMatchObject({
      statusLabel: 'Waiting for selected workflow',
      canResume: false,
      needsReview: false,
    });
  });

  it('offers retry only for a failed attempt with a reusable checkpoint', () => {
    expect(runControlState({ status: 'failed', retryAvailable: true })).toMatchObject({
      canRetry: true, canRestart: true,
    });
    expect(runControlState({ status: 'failed', retryAvailable: false })).toMatchObject({
      canRetry: false, canRestart: true,
    });
  });

  it('offers a fresh restart after completion or rejection', () => {
    expect(runControlState({ status: 'completed' }).canRestart).toBe(true);
    expect(runControlState({ status: 'rejected' }).canRestart).toBe(true);
  });

  it('disables actions while another control request is in progress', () => {
    expect(runControlState({ status: 'failed', retryAvailable: true, actionBusy: 'retry' }))
      .toMatchObject({ canRetry: false, canRestart: false });
  });
});

describe('attemptLabel', () => {
  it('includes reused step status', () => {
    expect(attemptLabel(2, 3)).toBe('Attempt 2 · 3 steps reused');
    expect(attemptLabel()).toBe('Attempt 1');
  });
});