import type { ReadinessSummary } from '../../../../api/types';
import { dumpYaml, type YamlWorkflow } from '../../yaml-bridge';

export function TechnicalTab({
  parsed,
  readiness,
  workflowName,
}: {
  parsed: YamlWorkflow;
  readiness: ReadinessSummary;
  workflowName: string;
}) {
  return (
    <div className="library-tab-content">
      <section className="library-technical-section">
        <h3>Nodes ({parsed.nodes.length})</h3>
        <ul className="library-technical-node-list">
          {parsed.nodes.map(node => (
            <li key={node.id}>
              <span className="library-technical-node-id">{node.id}</span>
              <span className="library-technical-node-type">{node.type}</span>
            </li>
          ))}
        </ul>
      </section>

      {readiness.items.length > 0 && (
        <section className="library-technical-section">
          <h3>Preflight checks</h3>
          <ul className="library-technical-checks">
            {readiness.items.map((item, index) => (
              <li key={`${item.code}-${index}`} className={`is-${item.severity}`}>
                <span className="library-technical-code">{item.code}</span> {item.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="library-technical-section">
        <h3>Raw workflow YAML — {workflowName}.yaml</h3>
        <pre className="library-technical-yaml">{dumpYaml(parsed)}</pre>
      </section>
    </div>
  );
}
