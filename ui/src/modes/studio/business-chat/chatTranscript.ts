import type { BusinessChatTranscriptMessage, WorkflowFileReference } from '../../../api/types';
import type { AssistantSegment, InterventionRequest } from './businessChatModel';

export type DurableChatMessage =
  | { id: string; role: 'user'; text: string; runId?: string | null; attachments?: WorkflowFileReference[] }
  | { id: string; role: 'assistant'; segments: AssistantSegment[]; runId: string | null; structuredResult?: unknown; responseLabel?: string }
  | { id: string; role: 'attempt'; text: string; runId?: string | null }
  | { id: string; role: 'error'; text: string; runId: string | null }
  | {
      id: string;
      role: 'intervention';
      request: InterventionRequest;
      status: 'pending' | 'resolved';
      resolution?: string;
    };

const SUMMARY_MAX_MESSAGES = 6;
const SUMMARY_MAX_MESSAGE_CHARS = 600;
const SUMMARY_MAX_CHARS = 2_400;

export function boundedConversationSummary(messages: DurableChatMessage[]): string {
  const lines = messages.flatMap(message => {
    if (message.role === 'user') return [`User: ${message.text.trim().slice(0, SUMMARY_MAX_MESSAGE_CHARS)}`];
    if (message.role !== 'assistant' || message.responseLabel?.includes('not grounded')) return [];
    const answer = message.segments
      .flatMap(segment => segment.kind === 'text' ? [segment.text] : [])
      .join('\n')
      .trim();
    return answer ? [`Assistant: ${answer.slice(0, SUMMARY_MAX_MESSAGE_CHARS)}`] : [];
  }).filter(line => !/^\w+:\s*$/.test(line));
  return lines.slice(-SUMMARY_MAX_MESSAGES).join('\n').slice(-SUMMARY_MAX_CHARS);
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function answerFromLegacyTechnicalText(text: string): string | null {
  const trimmed = text.trim();
  if (/^Rag\s*\n/i.test(trimmed)) {
    const match = trimmed.match(/(?:^|\n)Answer:\s*(.+?)(?=\n(?:Citations|Sources|Relevant Context|Answering Model|Resolved Answering Model|Retrievals|Grounding For Drafter|Retrieval Trace Id|Collection Id|Resolved Index Id):|$)/is);
    return match?.[1].trim() || null;
  }
  if (!/^Answer\s*\nRaw:\s*/i.test(trimmed)) return null;
  const raw = trimmed.replace(/^Answer\s*\nRaw:\s*/i, '').split(/\nParsed:/i, 1)[0].trim();
  try {
    const parsed = JSON.parse(raw) as unknown;
    const object = record(parsed);
    return object && typeof object.answer === 'string' && object.answer.trim()
      ? object.answer.trim()
      : null;
  } catch {
    return null;
  }
}

function isLegacyTechnicalText(text: string): boolean {
  const trimmed = text.trim();
  return /^(?:Start|Load Files|Read Sources|Rag|Answer|Reply)\s*\n/i.test(trimmed)
    || /\n(?:Raw|Parsed|Status|Defaulted|Data|Files|Image Files|Total Files|Text File Count|Image Count|Result):\s*/i.test(trimmed);
}

export function normalizePersistedAssistantSegments(segments: AssistantSegment[]): AssistantSegment[] {
  const recoveredAnswers = segments.flatMap(segment => {
    if (segment.kind !== 'text') return [];
    const answer = answerFromLegacyTechnicalText(segment.text);
    return answer ? [answer] : [];
  });
  if (recoveredAnswers.length === 0) return segments;
  const nonTechnical = segments.filter(segment => (
    segment.kind !== 'text'
    || !isLegacyTechnicalText(segment.text)
  ));
  return [
    { kind: 'text', text: recoveredAnswers[0] },
    ...nonTechnical.filter(segment => segment.kind !== 'text'),
  ];
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
    return { role: message.role, content: { segments: message.segments, ...(message.structuredResult !== undefined ? { structured_result: message.structuredResult } : {}), ...(message.responseLabel ? { response_label: message.responseLabel } : {}) }, run_id: message.runId };
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
    const segments = normalizePersistedAssistantSegments(content.segments as AssistantSegment[]);
    return {
      id: message.id,
      role: 'assistant',
      segments,
      runId: message.run_id,
      ...(content.structured_result !== undefined ? { structuredResult: content.structured_result } : {}),
      ...(typeof content.response_label === 'string' ? { responseLabel: content.response_label } : {}),
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
    const normalizedRequest = {
      ...request,
      gateId: typeof request.gateId === 'string' && request.gateId
        ? request.gateId
        : `${request.runId}:${request.nodeId}`,
    } as InterventionRequest;
    return {
      id: message.id,
      role: 'intervention',
      request: normalizedRequest,
      status: content.status === 'resolved' ? 'resolved' : 'pending',
      ...(typeof content.resolution === 'string' ? { resolution: content.resolution } : {}),
    };
  }
  return null;
}