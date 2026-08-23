import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createNote,
  createLocalChat,
  deleteLocalChat,
  ensureLocalChat,
  loadLocalChatHistory,
  consumePendingSources,
  loadNotes,
  loadWorkspaceSources,
  loadWorkspacePreferences,
  saveNotes,
  savePendingSources,
  saveWorkspacePreferences,
  saveWorkspaceSources,
  updateLocalChat,
} from './chatWorkspaceStorage';

function stubStorage(options: { throwOnWrite?: boolean } = {}) {
  const values = new Map<string, string>();
  vi.stubGlobal('window', {
    localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (options.throwOnWrite) throw new Error('storage unavailable');
        values.set(key, value);
      },
    },
    sessionStorage: {
      getItem: (key: string) => values.get(`session:${key}`) ?? null,
      setItem: (key: string, value: string) => {
        if (options.throwOnWrite) throw new Error('storage unavailable');
        values.set(`session:${key}`, value);
      },
      removeItem: (key: string) => { values.delete(`session:${key}`); },
    },
  });
  return values;
}

describe('Chat workspace storage', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('keeps notes isolated by workspace identity', () => {
    stubStorage();
    const note = createNote('Finding', 'Customers mention slow support.');
    saveNotes('chat-a', [note]);
    expect(loadNotes('chat-a')).toEqual([note]);
    expect(loadNotes('chat-b')).toEqual([]);
  });

  it('persists desktop panel preferences', () => {
    stubStorage();
    saveWorkspacePreferences({ sourcesCollapsed: true, sessionsCollapsed: false, sourcesWidth: 360, sessionWidth: 400, distractionFree: true });
    expect(loadWorkspacePreferences()).toEqual({ sourcesCollapsed: true, sessionsCollapsed: false, sourcesWidth: 360, sessionWidth: 400, distractionFree: true });
  });

  it('degrades safely when localStorage cannot be written', () => {
    stubStorage({ throwOnWrite: true });
    expect(() => saveNotes('chat-a', [createNote('A', 'B')])).not.toThrow();
    expect(() => saveWorkspacePreferences({ sourcesCollapsed: true, sessionsCollapsed: true, sourcesWidth: 304, sessionWidth: 332, distractionFree: false })).not.toThrow();
    expect(loadNotes('chat-a')).toEqual([]);
  });

  it('hands selected source presentation to the prepared workflow once', () => {
    stubStorage();
    const sources = [{ id: 'document:1', title: 'Policy.pdf', kind: 'document' as const, selected: true, status: 'ready' as const, collectionId: 'policies' }];
    savePendingSources('workflow-1', sources);
    expect(consumePendingSources('workflow-1')).toEqual(sources);
    expect(consumePendingSources('workflow-1')).toEqual([]);
  });

  it('restores source presentation after the one-time handoff is consumed', () => {
    stubStorage();
    const sources = [{ id: 'upload:1', title: 'Interview.docx', kind: 'upload' as const, selected: true, status: 'ready' as const }];
    saveWorkspaceSources('workflow:private:one', sources);
    expect(loadWorkspaceSources('workflow:private:one')).toEqual(sources);
  });

  it('creates, restores, renames and links browser-local chats', () => {
    stubStorage();
    const first = createLocalChat();
    const second = createLocalChat('Research notes');
    expect(ensureLocalChat(first.id).id).toBe(first.id);
    expect(loadLocalChatHistory().activeChatId).toBe(first.id);
    expect(updateLocalChat(first.id, { title: 'Customer interviews', workflowId: 'workflow-1', workflowSource: 'private', collectionId: 'collection-1', ragAgentId: 'rag-1', conversationId: 'conversation-1', runId: 'run-1' })).toMatchObject({
      title: 'Customer interviews', workflowId: 'workflow-1', collectionId: 'collection-1', ragAgentId: 'rag-1', conversationId: 'conversation-1', runId: 'run-1',
    });
    expect(loadLocalChatHistory().chats.map(chat => chat.id)).toEqual([second.id, first.id]);
  });

  it('deletes the active chat and restores the next local chat', () => {
    stubStorage();
    const first = createLocalChat('First');
    const second = createLocalChat('Second');
    expect(deleteLocalChat(second.id)?.id).toBe(first.id);
    expect(loadLocalChatHistory()).toMatchObject({ activeChatId: first.id, chats: [{ id: first.id }] });
  });
});