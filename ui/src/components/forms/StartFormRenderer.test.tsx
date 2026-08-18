import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { StartFormRenderer, type StartFormField, type StartFormFileField } from './StartFormRenderer';

function Harness({
  fields, fileFields = [], initialValues = {},
}: { fields: StartFormField[]; fileFields?: StartFormFileField[]; initialValues?: Record<string, unknown> }) {
  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  return (
    <StartFormRenderer
      fields={fields}
      fileFields={fileFields}
      interactive
      onChange={(name, value) => setValues(current => ({ ...current, [name]: value }))}
      values={values}
    />
  );
}

describe('StartFormRenderer — compound presets', () => {
  it('renders a currency preset as one grouped amount+currency row', () => {
    const field: StartFormField = {
      name: 'budget',
      label: 'Budget',
      type: 'object',
      preset: 'currency',
      units: ['USD', 'EUR'],
      fields: [
        { name: 'amount', type: 'number' },
        { name: 'currency', type: 'enum', enum_values: ['USD', 'EUR'] },
      ],
    };
    render(<Harness fields={[field]} />);
    expect(screen.getByText('Budget')).toBeInTheDocument();
    expect(screen.getAllByRole('combobox')).toHaveLength(1);
    expect(screen.getByText('USD')).toBeInTheDocument();
  });

  it('renders a date_range preset as a from/to pair', () => {
    const field: StartFormField = {
      name: 'window',
      type: 'object',
      preset: 'date_range',
      fields: [{ name: 'start', type: 'date' }, { name: 'end', type: 'date' }],
    };
    render(<Harness fields={[field]} />);
    expect(screen.getByText('From')).toBeInTheDocument();
    expect(screen.getByText('To')).toBeInTheDocument();
  });

  it('renders an address preset as its fixed set of sub-fields', () => {
    const field: StartFormField = {
      name: 'shipping_address',
      type: 'object',
      preset: 'address',
      fields: [
        { name: 'street', type: 'string' },
        { name: 'house_number', type: 'string' },
        { name: 'postal_code', type: 'string' },
        { name: 'city', type: 'string' },
        { name: 'country', type: 'string' },
      ],
    };
    render(<Harness fields={[field]} />);
    expect(screen.getByText('Street')).toBeInTheDocument();
    expect(screen.getByText('City')).toBeInTheDocument();
    expect(screen.getByText('Country')).toBeInTheDocument();
  });
});

describe('StartFormRenderer — repeating group', () => {
  const lineItems: StartFormField = {
    name: 'line_items',
    type: 'list',
    item_type: 'object',
    display: 'table',
    fields: [
      { name: 'product', type: 'string' },
      { name: 'quantity', type: 'integer' },
    ],
  };

  it('starts with no rows and adds one on "+ Add Row"', async () => {
    const user = userEvent.setup();
    render(<Harness fields={[lineItems]} />);
    expect(screen.queryByRole('button', { name: /remove row/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /add row/i }));
    expect(screen.getByRole('button', { name: /remove row 1/i })).toBeInTheDocument();
  });

  it('fills a row and removes it, producing the right output shape', async () => {
    const user = userEvent.setup();
    let latestValues: Record<string, unknown> = {};
    function Tracking() {
      const [values, setValues] = useState<Record<string, unknown>>({});
      latestValues = values;
      return (
        <StartFormRenderer
          fields={[lineItems]}
          fileFields={[]}
          interactive
          onChange={(name, value) => setValues(current => {
            const next = { ...current, [name]: value };
            latestValues = next;
            return next;
          })}
          values={values}
        />
      );
    }
    render(<Tracking />);

    await user.click(screen.getByRole('button', { name: /add row/i }));
    const productInput = screen.getAllByRole('textbox')[0];
    await user.type(productInput, 'Widget');

    expect(latestValues.line_items).toEqual([{ product: 'Widget' }]);

    await user.click(screen.getByRole('button', { name: /remove row 1/i }));
    expect(latestValues.line_items).toEqual([]);
  });
});

describe('StartFormRenderer — conditional visibility', () => {
  const requestKind: StartFormField = {
    name: 'request_kind',
    type: 'enum',
    enum_values: ['service', 'rfq'],
    label: 'Request kind',
  };
  const orderNumber: StartFormField = {
    name: 'order_number',
    type: 'string',
    label: 'Order number',
    visible_when: {
      operator: 'and',
      conditions: [{ field: 'request_kind', operator: 'equals', value: 'service' }],
    },
  };

  it('hides a conditional field until its condition is met, then shows it', async () => {
    const user = userEvent.setup();
    render(<Harness fields={[requestKind, orderNumber]} />);

    expect(screen.queryByText('Order number')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByRole('combobox'), 'service');
    expect(screen.getByText('Order number')).toBeInTheDocument();
  });

  it('hides it again if the condition later stops holding', async () => {
    const user = userEvent.setup();
    render(<Harness fields={[requestKind, orderNumber]} initialValues={{ request_kind: 'service' }} />);

    expect(screen.getByText('Order number')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox'), 'rfq');
    expect(screen.queryByText('Order number')).not.toBeInTheDocument();
  });
});
