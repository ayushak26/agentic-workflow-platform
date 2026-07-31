import { Routes, Route, Navigate } from 'react-router-dom';
import { StudioLayout } from './StudioLayout';
import { Library } from './Library';
import { Builder } from './Builder';
import { Cockpit } from './Cockpit';
import { RunHistory } from './RunHistory';
import { RunCandidates } from './RunCandidates';
import { ProposalReview } from './ProposalReview';
import { PipelineLibrary, PipelineRuns, PipelineRunView } from './Pipelines';

export function StudioRoot() {
  return (
    <Routes>
      <Route element={<StudioLayout />}>
        <Route index element={<Navigate to="library" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="builder/:name" element={<Builder />} />
        <Route path="builder" element={<Builder />} />
        <Route path="cockpit/:runId" element={<Cockpit />} />
        <Route path="history" element={<RunHistory />} />
        <Route path="history/:runId" element={<RunHistory />} />
        <Route path="candidates/:runId" element={<RunCandidates />} />
        <Route path="pipelines" element={<PipelineLibrary />} />
        <Route path="pipelines/runs" element={<PipelineRuns />} />
        <Route path="pipelines/runs/:pipelineRunId" element={<PipelineRunView />} />
        <Route path="proposal-review" element={<ProposalReview />} />
        <Route path="proposal-review/:runId" element={<ProposalReview />} />
      </Route>
    </Routes>
  );
}
