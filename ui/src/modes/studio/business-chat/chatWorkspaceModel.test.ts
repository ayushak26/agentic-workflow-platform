import { describe, expect, it } from 'vitest';
import { activitySummary, artifactPrompt, collectionAsSource, friendlyError, httpUrlsInText, selectedCollectionId, webSourcesFromText } from './chatWorkspaceModel';

describe('chat workspace presentation model', () => {
  it('only returns a collection when selected sources share one collection', () => {
    expect(selectedCollectionId([
      { id: 'a', title: 'A', kind: 'document', selected: true, status: 'ready', collectionId: 'one' },
      { id: 'b', title: 'B', kind: 'document', selected: true, status: 'ready', collectionId: 'one' },
    ])).toBe('one');
    expect(selectedCollectionId([
      { id: 'a', title: 'A', kind: 'document', selected: true, status: 'ready', collectionId: 'one' },
      { id: 'b', title: 'B', kind: 'document', selected: true, status: 'ready', collectionId: 'two' },
    ])).toBeNull();
  });

  it('keeps the selected Knowledge collection as an explicit source', () => {
    expect(collectionAsSource({
      collection_id: 'pump-docs', name: 'Pump ICP2', description: '', status: 'ready',
      document_count: 4, chunk_count: 20, active_index_id: 'index-1', metadata_schema: {}, doc_types: [],
    })).toMatchObject({
      id: 'collection:pump-docs', title: 'Pump ICP2', subtitle: '4 documents · ready',
      kind: 'collection', selected: true, collectionId: 'pump-docs',
    });
  });

  it('projects technical failures into recoverable language', () => {
    expect(friendlyError('MCP connection unavailable').title).toBe('A connected service is unavailable');
    expect(friendlyError('ambiguous customer identity').actions).toContain('Select manually');
    expect(friendlyError("I couldn't complete this request: RETRIEVAL_TIMEOUT: Knowledge retrieval exceeded its deadline.")).toEqual({
      title: 'Knowledge search took too long',
      message: 'Your sources and conversation are safe. Retry the Knowledge search; completed workflow steps can be reused when a checkpoint is available.',
      actions: ['Retry Knowledge search'],
    });
  });

  it('summarizes activity without exposing node names', () => {
    expect(activitySummary([{ nodeId: 'x', nodeType: 'MCPToolAgent', displayName: 'CRM', agentRole: null, status: 'running', text: 'Checking customer records…', recoveryActions: [] }])).toBe('Checking customer records…');
  });

  it('turns create choices into natural-language requests', () => {
    expect(artifactPrompt('presentation', 'churn risks', true)).toContain('executive presentation');
    expect(artifactPrompt('presentation', 'churn risks', true)).toContain('churn risks');
  });

  it('normalizes pasted web URLs without claiming they were indexed', () => {
    expect(httpUrlsInText('Compare https://example.com/report, and https://example.com/report.')).toEqual([
      'https://example.com/report',
    ]);
    expect(webSourcesFromText('Read https://example.com/research/')[0]).toMatchObject({
      id: 'web:https://example.com/research/',
      title: 'example.com/research',
      subtitle: 'Web source · used with this request',
      kind: 'web',
      selected: true,
    });
  });
});