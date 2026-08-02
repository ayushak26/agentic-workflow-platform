import type { LibraryMetadata } from '../../../../api/types';
import { humanizeIdentifier } from '../../guided/runtime-model';
import type { YamlWorkflow } from '../../yaml-bridge';

type OutputNodeRef = { node_id: string; flatten?: boolean };

function outputNodeIds(parsed: YamlWorkflow): OutputNodeRef[] {
  const output = parsed.output as { nodes?: OutputNodeRef[] } | undefined;
  return Array.isArray(output?.nodes) ? output!.nodes! : [];
}

export function WhatItProducesTab({
  parsed,
  library,
}: {
  parsed: YamlWorkflow;
  library: LibraryMetadata;
}) {
  const nodesById = new Map(parsed.nodes.map(node => [node.id, node]));
  const finalOutputs = outputNodeIds(parsed);

  return (
    <div className="library-tab-content">
      {library.outputs.length > 0 && (
        <section className="library-produces-section">
          <h3>Final deliverables</h3>
          <div className="library-chip-row">
            {library.outputs.map(output => (
              <span className="library-output-chip" key={output}>{output.toUpperCase()}</span>
            ))}
          </div>
          {!library.declared && (
            <p className="library-empty-note">
              Inferred from node names, not declared — treat as a best guess
              until this workflow&apos;s Library metadata confirms it.
            </p>
          )}
        </section>
      )}

      <section className="library-produces-section">
        <h3>Workflow outputs</h3>
        {finalOutputs.length === 0 ? (
          <p className="library-empty-note">
            This workflow doesn&apos;t declare an explicit output projection —
            check Technical details for its full node list.
          </p>
        ) : (
          <ul className="library-produces-list">
            {finalOutputs.map(ref => {
              const node = nodesById.get(ref.node_id);
              const experience = node?.experience;
              return (
                <li key={ref.node_id}>
                  <div className="library-produces-name">
                    {experience?.display_name || humanizeIdentifier(ref.node_id)}
                  </div>
                  <p className="library-produces-description">
                    {experience?.expected_output
                      || 'Expected result not yet described — open Technical details for the raw output shape.'}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <div className="library-empty-note">
        Every output here is available once the run reaches Completed —
        working/intermediate results appear in Guided Run while the workflow
        is still in progress.
      </div>
    </div>
  );
}
