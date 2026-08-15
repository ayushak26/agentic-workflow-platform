import type {
  BusinessAction,
  BusinessActionType,
  BusinessActivityView,
  BusinessAttentionItem,
  BusinessFact,
  BusinessProjection,
} from '../../../api/types';

/**
 * The BASF RFQ from the redesign brief, in the shape the server produces.
 *
 * Shared by the Business View tests so they assert against one realistic work
 * item rather than a different hand-built stub each — and so a change to the
 * projection contract breaks them all at once, which is the point.
 */

export function action(
  type: BusinessActionType,
  label: string,
  overrides: Partial<BusinessAction> = {},
): BusinessAction {
  return {
    id: `${type}:${label}`,
    type,
    label,
    description: null,
    emphasis: 'secondary',
    enabled: true,
    disabled_reason: null,
    requires_approval: false,
    params: {},
    ...overrides,
  };
}

export function fact(overrides: Partial<BusinessFact> & { id: string; label: string; display: string }): BusinessFact {
  return {
    value: overrides.display,
    source: 'ai',
    source_label: 'AI · claude-sonnet-4-5',
    node_id: 'understand_message',
    editable: false,
    stale: false,
    missing: false,
    actions: [],
    ...overrides,
  };
}

export function attentionItem(
  overrides: Partial<BusinessAttentionItem> & { id: string; title: string },
): BusinessAttentionItem {
  return {
    detail: null,
    severity: 'info',
    status_label: 'Missing',
    field: null,
    actions: [],
    ...overrides,
  };
}

export function activity(
  overrides: Partial<BusinessActivityView> & { id: string; title: string },
): BusinessActivityView {
  return {
    status: 'completed',
    status_label: 'Completed',
    summary: null,
    kind: 'rule',
    kind_label: 'Business rules',
    facts: [],
    actions: [],
    source_nodes: [],
    ai: null,
    started_at: null,
    completed_at: '2026-08-14T17:21:07Z',
    duration_ms: null,
    technical: {
      node_ids: [],
      nodes: [],
      ai_calls: [],
      rule_count: 0,
      rules: [],
      duration_ms: null,
      has_raw_output: true,
    },
    ...overrides,
  };
}

export function basfProjection(overrides: Partial<BusinessProjection> = {}): BusinessProjection {
  return {
    work_item: {
      id: 'run-123',
      title: 'BASF SE — Quotation request',
      type: 'Quotation request',
      reference: 'run-123',
      started_at: '2026-08-14T17:21:00Z',
      updated_at: '2026-08-14T17:21:12Z',
      assigned_to: null,
      customer: 'BASF SE',
    },
    process: { name: 'Pump Manufacturer Case Routing', goal: 'Route the request to the right team.' },
    status: 'completed',
    business_status: {
      code: 'ready_for_team',
      headline: 'Ready for Inside Sales',
      summary:
        'BASF is requesting a quotation for five new pumps based on an attached datasheet and spare parts related to order SO 231706.',
      tone: 'done',
      attention_count: 4,
      narration_source: 'deterministic',
      narration_model: null,
      state_version: 'v1',
    },
    attention: [
      attentionItem({
        id: 'attention:missing:pump_model',
        title: 'Pump model',
        severity: 'warning',
        field: 'pump_model',
        actions: [
          action('document_review', 'Review datasheet'),
          action('edit_fact', 'Enter manually', { params: { field: 'pump_model' } }),
        ],
      }),
      attentionItem({
        id: 'attention:missing:requested_delivery_date',
        title: 'Requested delivery date',
        field: 'requested_delivery_date',
        actions: [
          action('draft_clarification', 'Ask customer', { requires_approval: true }),
          action('edit_fact', 'Enter manually', { params: { field: 'requested_delivery_date' } }),
        ],
      }),
    ],
    understanding: {
      node_id: 'understand_message',
      summary: 'BASF requests a quotation for 5 pumps and spare parts related to SO 231706.',
      confidence: 0.86,
      fields: [
        fact({ id: 'understanding:customer_name', label: 'Customer', display: 'BASF SE' }),
        fact({ id: 'understanding:intent', label: 'Request', display: 'Quotation request' }),
        fact({ id: 'understanding:requested_quantity', label: 'Quantity', display: '5', editable: true,
               actions: [action('edit_fact', 'Edit', { params: { field: 'requested_quantity' } })] }),
        fact({ id: 'understanding:pump_model', label: 'Pump model', display: 'Not stated', missing: true }),
      ],
      source: 'ai',
      source_label: 'AI · claude-sonnet-4-5',
      ai: {
        requested: 'auto', selected: 'claude-sonnet-4-5', executed: 'claude-sonnet-4-5',
        fallback: false, fallback_reason: null, routing_reason: null,
        latency_ms: 1400, cost_usd: 0.0018, task_type: 'extraction', provider: 'anthropic', call_count: 1,
      },
      actions: [action('open_technical_details', 'View technical details', { params: { activity_id: 'understand' } })],
    },
    activities: [
      activity({
        id: 'understand', title: 'Request understood', kind: 'ai', kind_label: 'AI · claude-sonnet-4-5',
        summary: 'BASF requests a quotation for 5 pumps and spare parts related to SO 231706.',
        source_nodes: ['understand_message'],
      }),
      activity({
        id: 'handling', title: 'Handling checks completed', source_nodes: ['intent_router', 'safety_router'],
        technical: {
          node_ids: ['intent_router', 'safety_router'], nodes: [], ai_calls: [],
          rule_count: 2, rules: [], duration_ms: 12, has_raw_output: true,
        },
        facts: [
          fact({ id: 'check:safety_router', label: 'Safety issue', display: 'No' }),
          fact({ id: 'check:intent_router', label: 'Request', display: 'Quotation request' }),
        ],
        actions: [action('open_technical_details', 'Technical details', { params: { activity_id: 'handling' } })],
      }),
    ],
    happened: ['Request: Quotation request', 'Safety issue: No', 'Routed to Inside Sales'],
    facts: [],
    decision: {
      id: 'handling_decision',
      headline: 'Inside Sales',
      summary: 'Standard RFQ',
      reason: 'Standard enquiry and no named territory owner was returned by CRM.',
      source: 'rule',
      source_label: 'Business rule',
      facts: [fact({ id: 'check:safety_router', label: 'Safety issue', display: 'No', source: 'rule', source_label: 'Business rule' })],
      rules: [{ id: 'intent_router', name: 'Request → RFQ', description: 'rule matched', node_id: 'intent_router', matched: true }],
      actions: [action('explain_decision', 'Why?'), action('route_override', 'Change route'), action('assign_work_item', 'Assign owner')],
      node_ids: ['intent_router'],
      overridden: false,
      overridden_by: null,
      overridden_at: null,
      original_headline: null,
      stale: false,
    },
    recommended_actions: [action('document_review', 'Review datasheet')],
    other_actions: [action('add_note', 'Add note')],
    next_step: {
      headline: 'Inside Sales takes this on',
      description: 'Inside Sales reviews the technical requirements and prepares the quotation.',
      blocked: false,
      blocked_reason: null,
      owner: 'Inside Sales',
      actions: [action('assign_work_item', 'Assign owner')],
    },
    related_records: [
      {
        id: 'record:sales_order_reference', kind: 'order', label: 'Sales order', reference: 'SO 231706',
        source: 'customer_message', source_label: 'Customer message',
        actions: [action('open_related_record', 'Open SO 231706'), action('related_record_lookup', 'Get order details')],
      },
    ],
    attachments: [],
    timeline: [
      { id: 't1', ts: '2026-08-14T17:21:00Z', title: 'Request received', detail: null, marks: [], kind: 'status', source: 'customer_message', source_label: 'Customer message' },
      { id: 't2', ts: '2026-08-14T17:21:04Z', title: 'Request understood', detail: null, marks: [], kind: 'activity', source: 'ai', source_label: 'AI · claude-sonnet-4-5' },
      {
        id: 't3', ts: '2026-08-14T17:21:07Z', title: 'Handling checks completed', detail: null,
        marks: ['Request: Quotation request', 'Complexity: Standard', 'Safety issue: No'],
        kind: 'activity', source: 'rule', source_label: 'Business rules',
      },
    ],
    allowed_actions: [
      action('assign_work_item', 'Assign owner'),
      action('stop_run', 'Stop', { emphasis: 'danger' }),
      action('open_technical_details', 'Technical details', { params: { activity_id: 'run' } }),
    ],
    required_user_actions: [],
    suggested_questions: ['Why Inside Sales?', 'What is missing?'],
    activity_summary: { completed: 2, total: 2, technical_nodes: 3 },
    ...overrides,
  };
}
