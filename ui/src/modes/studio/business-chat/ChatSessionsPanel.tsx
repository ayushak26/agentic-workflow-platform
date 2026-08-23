import type { LocalChatRecord } from './chatWorkspaceStorage';

export function ChatSessionsPanel({ chats, activeChatId, collapsed, onCollapse, onNew, onOpen, onRename, onDelete }: {
  chats: LocalChatRecord[];
  activeChatId: string;
  collapsed: boolean;
  onCollapse: () => void;
  onNew: () => void;
  onOpen: (chat: LocalChatRecord) => void;
  onRename: (chat: LocalChatRecord) => void;
  onDelete: (chat: LocalChatRecord) => void;
}) {
  if (collapsed) return <aside className="chat-rail chat-rail--collapsed chat-rail--right"><button type="button" onClick={onCollapse} aria-label="Expand sessions">‹</button><span>Sessions</span></aside>;
  return <aside className="chat-rail chat-sessions" aria-label="Sessions panel"><div className="chat-rail-header"><div><h2>Sessions</h2><p>Stored in this browser</p></div><button type="button" onClick={onCollapse} aria-label="Collapse sessions">›</button></div><div className="chat-sessions-actions"><button type="button" className="chat-primary-small" onClick={onNew}>＋ New session</button></div><div className="chat-session-list">{chats.map(chat => <div key={chat.id} className={`chat-session-row ${chat.id === activeChatId ? 'is-active' : ''}`}><button type="button" className="chat-session-open" onClick={() => onOpen(chat)}><strong>{chat.title}</strong><small>{chat.workflowId ? 'Conversation' : 'Draft'} · {new Date(chat.updatedAt).toLocaleDateString()}</small></button><div><button type="button" aria-label={`Rename ${chat.title}`} onClick={() => onRename(chat)}>Rename</button><button type="button" aria-label={`Delete ${chat.title}`} onClick={() => onDelete(chat)}>Delete</button></div></div>)}</div></aside>;
}