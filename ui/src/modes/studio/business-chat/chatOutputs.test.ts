import { describe, expect, it } from 'vitest';

import type { WorkflowFileReference } from '../../../api/types';
import {
  EMPTY_CHAT_RESULT,
  normalizeChatOutputs,
  splitFencedCode,
  structuredValueAsText,
} from './chatOutputs';

const fileRef = (overrides: Partial<WorkflowFileReference> = {}): WorkflowFileReference => ({
  kind: 'workflow_file',
  file_id: 'file-1',
  name: 'upload.png',
  extension: '.png',
  category: 'image',
  content_type: 'image/png',
  size_bytes: 2048,
  sha256: 'sha',
  minio_key: 'workflow-input-files/user/file-1/upload.png',
  parseable_text: false,
  ...overrides,
});

describe('normalizeChatOutputs', () => {
  it('maps an End chat response to text', () => {
    expect(normalizeChatOutputs({
      node_runs: { end: { output: { chat_message: 'Finished.' } } },
      node_types: { end: 'EndAgent' },
    })).toEqual([{ kind: 'text', text: 'Finished.' }]);
  });

  it('splits fenced Python and multiple fenced blocks in source order', () => {
    expect(splitFencedCode('Before\n```python\nprint(1)\n```\nBetween\n```sql\nselect 1;\n```')).toEqual([
      { kind: 'text', text: 'Before' },
      { kind: 'code', code: 'print(1)', language: 'python' },
      { kind: 'text', text: 'Between' },
      { kind: 'code', code: 'select 1;', language: 'sql' },
    ]);
  });

  it('maps a generated image key to an image', () => {
    const result = normalizeChatOutputs({
      node_runs: {
        image: { output: {
          generated: true,
          minio_key: 'workflows/run/image.png',
          content_type: 'image/png',
          provider: 'openrouter',
          model: 'image-model',
        } },
      },
    });
    expect(result).toEqual([expect.objectContaining({
      kind: 'image', key: 'workflows/run/image.png', provider: 'openrouter', model: 'image-model',
    })]);
  });

  it('maps an uploaded image reference to an image', () => {
    const result = normalizeChatOutputs({ outputs: { image: fileRef() } });
    expect(result).toEqual([expect.objectContaining({
      kind: 'image', title: 'upload.png', reference: expect.objectContaining({ file_id: 'file-1' }),
    })]);
  });

  it('maps PDF renderer keys and PDF file references to PDFs', () => {
    const renderer = normalizeChatOutputs({
      node_runs: { render: { output: { pdf_key: 'workflows/run/report.pdf', page_count: 4 } } },
    });
    expect(renderer).toEqual([expect.objectContaining({ kind: 'pdf', pageCount: 4 })]);

    const reference = normalizeChatOutputs({ outputs: { report: fileRef({
      name: 'report.pdf', extension: '.pdf', category: 'document', content_type: 'application/pdf',
      minio_key: 'workflow-input-files/user/file-1/report.pdf',
    }) } });
    expect(reference).toEqual([expect.objectContaining({ kind: 'pdf', title: 'report.pdf' })]);
  });

  it('converts structured JSON and table rows to readable text', () => {
    const structured = normalizeChatOutputs({ outputs: { result: { customer_count: 18, highest_growth: 'Acme' } } });
    expect(structured).toEqual([{ kind: 'text', text: 'Result\nCustomer Count: 18\nHighest Growth: Acme' }]);
    expect(structured[0]).not.toHaveProperty('text', expect.stringContaining('{'));

    expect(structuredValueAsText({ rows: [
      { customer: 'Acme', growth: '47%' },
      { customer: 'Globex', growth: '42%' },
    ] })).toContain('Found 2 rows.');
  });

  it('prefers a PDF over a DOCX sibling from the same renderer output', () => {
    const result = normalizeChatOutputs({
      node_runs: { render: { output: {
        pdf_key: 'workflows/run/proposal.pdf',
        docx_key: 'workflows/run/proposal.docx',
      } } },
    });
    expect(result.map(item => item.kind)).toEqual(['pdf']);
  });

  it('prefers a PDF over a DOCX sibling in a file-reference array', () => {
    const result = normalizeChatOutputs({ outputs: { documents: [
      fileRef({
        file_id: 'docx', name: 'proposal.docx', extension: '.docx', category: 'document',
        content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        minio_key: 'workflow-input-files/user/docx/proposal.docx',
      }),
      fileRef({
        file_id: 'pdf', name: 'proposal.pdf', extension: '.pdf', category: 'document',
        content_type: 'application/pdf', minio_key: 'workflow-input-files/user/pdf/proposal.pdf',
      }),
    ] } });
    expect(result.map(item => item.kind)).toEqual(['pdf']);
  });

  it.each([
    ['docx', 'workflows/run/report.docx'],
    ['pptx', 'workflows/run/deck.pptx'],
    ['xlsx', 'workflows/run/data.xlsx'],
  ] as const)('keeps a lone %s artifact as a primary output', (kind, key) => {
    expect(normalizeChatOutputs({ node_runs: { render: { output: { minio_key: key } } } }))
      .toEqual([expect.objectContaining({ kind, key })]);
  });

  it('uses readable text for unknown objects', () => {
    expect(normalizeChatOutputs({ outputs: { result: { status: 'ready', count: 2 } } }))
      .toEqual([{ kind: 'text', text: 'Result\nStatus: ready\nCount: 2' }]);
  });

  it('emits explicit text when there is no meaningful result', () => {
    expect(normalizeChatOutputs({ outputs: { result: null }, node_runs: {} }))
      .toEqual([{ kind: 'text', text: EMPTY_CHAT_RESULT }]);
  });
});