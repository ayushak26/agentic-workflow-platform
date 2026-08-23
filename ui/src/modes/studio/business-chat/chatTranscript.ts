import type { BusinessChatTranscriptMessage, WorkflowFileReference } from '../../../api/types';
import type { AssistantSegment, InterventionRequest } from './businessChatModel';

export type DurableChatMessage =
  | { id: string; role: 'user'; text: string; runId?: string | null; attachments?: WorkflowFileReference[] }
  | { id: string; role: 'assistant'; segments: AssistantSegment[]; runId: string | null }
  | { id: string; role: 'attempt'; text: string; runId?: string | null }
  | { id: string; role: 'error'; text: string; runId: string | null }
  | {
      id: string;
      role: 'intervention';
      request: InterventionRequest;
      status: 'pending' | 'resolved';
      resolution?: string;
    };

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function serializeDurableMessage(message: DurableChatMessage): {
  role: DurableChatMessage['role'];
  content: Record<string, unknown>;
  run_id: string | null;
} {
  if (message.role === 'user') {
    return {
      role: message.role,
      content: { text: message.text, attachments: message.attachments ?? [] },
      run_id: message.runId ?? null,
    };
  }
  if (message.role === 'assistant') {
    return { role: message.role, content: { segments: message.segments }, run_id: message.runId };
  }
  if (message.role === 'error') {
    return { role: message.role, content: { text: message.text }, run_id: message.runId };
  }
  if (message.role === 'attempt') {
    return { role: message.role, content: { text: message.text }, run_id: message.runId ?? null };
  }
  return {
    role: message.role,
    content: {
      request: message.request,
      status: message.status,
      ...(message.resolution ? { resolution: message.resolution } : {}),
    },
    run_id: message.request.runId,
  };
}

export function deserializeDurableMessage(
  message: BusinessChatTranscriptMessage,
): DurableChatMessage | null {
  const content = record(message.content);
  if (!content) return null;
  if (message.role === 'user' && typeof content.text === 'string') {
    return {
      id: message.id,
      role: 'user',
      text: content.text,
      ...(message.run_id ? { runId: message.run_id } : {}),
      ...(Array.isArray(content.attachments)
        ? { attachments: content.attachments as WorkflowFileReference[] }
        : {}),
    };
  }
  if (message.role === 'assistant' && Array.isArray(content.segments)) {
    return {
      id: message.id,
      role: 'assistant',
      segments: content.segments as AssistantSegment[],
      runId: message.run_id,
    };
  }
  if (message.role === 'attempt' && typeof content.text === 'string') {
    return {
      id: message.id,
      role: 'attempt',
      text: content.text,
      ...(message.run_id ? { runId: message.run_id } : {}),
    };
  }
  if (message.role === 'error' && typeof content.text === 'string') {
    return { id: message.id, role: 'error', text: content.text, runId: message.run_id };
  }
  if (message.role === 'intervention') {
    const request = record(content.request);
    if (!request || typeof request.runId !== 'string' || typeof request.nodeId !== 'string') return null;
    return {
      id: message.id,
      role: 'intervention',
      request: request as InterventionRequest,
      status: content.status === 'resolved' ? 'resolved' : 'pending',
      ...(typeof content.resolution === 'string' ? { resolution: content.resolution } : {}),
    };
  }
  return null;
}