import type { YamlWorkflow } from '../../yaml-bridge';

const TYPE_LABEL: Record<string, string> = {
  file: 'File upload',
  text: 'Text',
  json: 'Structured data',
};

export function WhatYouNeedTab({ parsed }: { parsed: YamlWorkflow }) {
  const entries = Object.entries(parsed.inputs ?? {});
  const required = entries.filter(([, spec]) => spec.required);
  const optional = entries.filter(([, spec]) => !spec.required);

  if (entries.length === 0) {
    return (
      <div className="library-tab-content">
        <div className="library-empty-note">
          This workflow needs no inputs from you to start — it runs from
          whatever is already configured inside it.
        </div>
      </div>
    );
  }

  return (
    <div className="library-tab-content">
      <section className="library-needs-section">
        <h3>Required before starting</h3>
        {required.length === 0 && <p className="library-empty-note">Nothing is required.</p>}
        <ul className="library-needs-list">
          {required.map(([name, spec]) => (
            <li key={name}>
              <div className="library-needs-name">{name.replace(/_/g, ' ')}</div>
              <div className="library-needs-meta">
                {TYPE_LABEL[spec.type] ?? spec.type}
                {spec.type === 'file' && spec.accept && spec.accept.length > 0 && (
                  <> · accepts {spec.accept.join(', ')}</>
                )}
                {spec.type === 'file' && spec.multiple && spec.max_files && (
                  <> · up to {spec.max_files} files</>
                )}
              </div>
              {spec.description && <p className="library-needs-description">{spec.description}</p>}
            </li>
          ))}
        </ul>
      </section>

      <section className="library-needs-section">
        <h3>Optional</h3>
        {optional.length === 0 && <p className="library-empty-note">Nothing optional is defined.</p>}
        <ul className="library-needs-list">
          {optional.map(([name, spec]) => (
            <li key={name}>
              <div className="library-needs-name">{name.replace(/_/g, ' ')}</div>
              <div className="library-needs-meta">{TYPE_LABEL[spec.type] ?? spec.type}</div>
              {spec.description && <p className="library-needs-description">{spec.description}</p>}
            </li>
          ))}
        </ul>
      </section>

      <div className="library-empty-note">
        Detailed input schemas (accepted MIME types, size limits) remain
        available in Technical details.
      </div>
    </div>
  );
}
