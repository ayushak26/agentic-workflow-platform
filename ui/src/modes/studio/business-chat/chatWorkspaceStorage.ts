import type { WorkspaceNote, WorkspaceSource } from './chatWorkspaceModel';

const PREFIX = 'eurskem.chat.notes.';
const PREFERENCES_KEY = 'eurskem.chat.workspace.preferences';
const SOURCE_HANDOFF_PREFIX = 'eurskem.chat.pending-sources:';
const SOURCES_PREFIX = 'eurskem.chat.sources.';
const CHAT_HISTORY_KEY = 'eurskem.chat.local-history.v1';

export type LocalChatRecord = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  workflowId: string | null;
  workflowSource: 'private' | 'shared' | null;
  isGeneralChat?: boolean;
  collectionId?: string | null;
  ragAgentId?: string | null;
  conversationId: string | null;
  runId: string | null;
};

type LocalChatHistory = { activeChatId: string | null; chats: LocalChatRecord[] };

function emptyHistory(): LocalChatHistory { return { activeChatId: null, chats: [] }; }

export function loadLocalChatHistory(): LocalChatHistory {
  try {
    const value = window.localStorage.getItem(CHAT_HISTORY_KEY);
    if (!value) return emptyHistory();
    const parsed = JSON.parse(value) as Partial<LocalChatHistory>;
    return {
      activeChatId: typeof parsed.activeChatId === 'string' ? parsed.activeChatId : null,
      chats: Array.isArray(parsed.chats) ? parsed.chats : [],
    };
  } catch { return emptyHistory(); }
}

function saveLocalChatHistory(history: LocalChatHistory): void {
  try { window.localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(history)); } catch { /* optional local history */ }
}

export function createLocalChat(title = 'New chat'): LocalChatRecord {
  const now = new Date().toISOString();
  const chat: LocalChatRecord = {
    id: `chat-${crypto.randomUUID()}`, title, createdAt: now, updatedAt: now,
    workflowId: null, workflowSource: null, isGeneralChat: false,
    collectionId: null, ragAgentId: null, conversationId: null, runId: null,
  };
  const history = loadLocalChatHistory();
  saveLocalChatHistory({ activeChatId: chat.id, chats: [chat, ...history.chats] });
  return chat;
}

export function ensureLocalChat(requestedId?: string | null): LocalChatRecord {
  const history = loadLocalChatHistory();
  const existing = history.chats.find(chat => chat.id === requestedId)
    ?? history.chats.find(chat => chat.id === history.activeChatId)
    ?? history.chats[0];
  if (!existing) return createLocalChat();
  if (history.activeChatId !== existing.id) saveLocalChatHistory({ ...history, activeChatId: existing.id });
  return existing;
}

export function updateLocalChat(chatId: string, patch: Partial<Omit<LocalChatRecord, 'id' | 'createdAt'>>): LocalChatRecord | null {
  const history = loadLocalChatHistory();
  let updated: LocalChatRecord | null = null;
  const chats = history.chats.map(chat => {
    if (chat.id !== chatId) return chat;
    updated = { ...chat, ...patch, updatedAt: patch.updatedAt ?? new Date().toISOString() };
    return updated;
  });
  if (updated) saveLocalChatHistory({ activeChatId: chatId, chats });
  return updated;
}

export function deleteLocalChat(chatId: string): LocalChatRecord | null {
  const history = loadLocalChatHistory();
  const chats = history.chats.filter(chat => chat.id !== chatId);
  const next = chats[0] ?? null;
  saveLocalChatHistory({ activeChatId: next?.id ?? null, chats });
  return next;
}

export type ChatWorkspacePreferences = {
  sourcesCollapsed: boolean;
  sessionsCollapsed: boolean;
  sourcesWidth: number;
  sessionWidth: number;
  distractionFree: boolean;
};

export function loadNotes(workspaceId: string): WorkspaceNote[] {
  try {
    const value = window.localStorage.getItem(`${PREFIX}${workspaceId}`);
    return value ? JSON.parse(value) as WorkspaceNote[] : [];
  } catch {
    return [];
  }
}

export function saveNotes(workspaceId: string, notes: WorkspaceNote[]): void {
  try {
    window.localStorage.setItem(`${PREFIX}${workspaceId}`, JSON.stringify(notes));
  } catch {
    // Browser-local notes are optional; storage failures must not block Chat.
  }
}

export function loadWorkspacePreferences(): ChatWorkspacePreferences {
  try {
    const value = window.localStorage.getItem(PREFERENCES_KEY);
    if (!value) return { sourcesCollapsed: false, sessionsCollapsed: false, sourcesWidth: 304, sessionWidth: 332, distractionFree: false };
    const parsed = JSON.parse(value) as Partial<ChatWorkspacePreferences> & { studioCollapsed?: boolean };
    return {
      sourcesCollapsed: parsed.sourcesCollapsed === true,
      sessionsCollapsed: parsed.sessionsCollapsed === true || parsed.studioCollapsed === true,
      sourcesWidth: typeof parsed.sourcesWidth === 'number' ? Math.min(440, Math.max(240, parsed.sourcesWidth)) : 304,
      sessionWidth: typeof parsed.sessionWidth === 'number' ? Math.min(520, Math.max(280, parsed.sessionWidth)) : 332,
      distractionFree: parsed.distractionFree === true,
    };
  } catch {
    return { sourcesCollapsed: false, sessionsCollapsed: false, sourcesWidth: 304, sessionWidth: 332, distractionFree: false };
  }
}

export function saveWorkspacePreferences(preferences: ChatWorkspacePreferences): void {
  try {
    window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences));
  } catch {
    // Collapse state is a convenience only.
  }
}

export function createNote(title: string, body: string): WorkspaceNote {
  const now = new Date().toISOString();
  return {
    id: `note-${crypto.randomUUID()}`,
    title: title.trim() || 'Untitled note',
    body: body.trim(),
    createdAt: now,
    updatedAt: now,
  };
}

export function savePendingSources(workflowId: string, sources: WorkspaceSource[]): void {
  try {
    window.sessionStorage.setItem(`${SOURCE_HANDOFF_PREFIX}${workflowId}`, JSON.stringify(sources));
  } catch {
    // The run still receives uploaded files through the existing handoff. This
    // metadata only preserves their presentation in the active workspace.
  }
}

export function consumePendingSources(workflowId: string): WorkspaceSource[] {
  const key = `${SOURCE_HANDOFF_PREFIX}${workflowId}`;
  try {
    const value = window.sessionStorage.getItem(key);
    if (!value) return [];
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed) ? parsed as WorkspaceSource[] : [];
  } catch {
    return [];
  } finally {
    try { window.sessionStorage.removeItem(key); } catch { /* optional handoff */ }
  }
}

export function loadWorkspaceSources(workspaceId: string): WorkspaceSource[] {
  try {
    const value = window.localStorage.getItem(`${SOURCES_PREFIX}${workspaceId}`);
    if (!value) return [];
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed) ? parsed as WorkspaceSource[] : [];
  } catch {
    return [];
  }
}

export function saveWorkspaceSources(workspaceId: string, sources: WorkspaceSource[]): void {
  try {
    window.localStorage.setItem(`${SOURCES_PREFIX}${workspaceId}`, JSON.stringify(sources));
  } catch {
    // Source presentation can be reconstructed from future selections; a
    // storage failure must not affect workflow execution.
  }
}