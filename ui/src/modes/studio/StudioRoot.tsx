import { Routes, Route, Navigate, useParams } from 'react-router-dom';
import { StudioLayout } from './StudioLayout';
import { Library } from './Library';
import { Builder } from './Builder';
import { Cockpit } from './Cockpit';
import { BusinessView } from './BusinessView';
import { BusinessChat } from './business-chat/BusinessChat';
import { RunHistory } from './RunHistory';
import { RunCandidates } from './RunCandidates';
import { ProposalReview } from './ProposalReview';
import { PipelineLibrary, PipelineRuns, PipelineRunView } from './Pipelines';

// Guided Run was replaced by Business View; this keeps any old bookmarked
// or cached `/guided/:runId` link working rather than 404ing.
function GuidedRunRedirect() {
  const { runId } = useParams();
  return <Navigate to={`/business/${runId}`} replace />;
}

export function StudioRoot() {
  return (
    <Routes>
      <Route element={<StudioLayout />}>
        <Route index element={<Navigate to="chat" replace />} />
        <Route path="my-work" element={<Navigate to="/chat" replace />} />
        <Route path="chat" element={<BusinessChat />} />
        <Route path="chat/shared/:workflowName" element={<BusinessChat />} />
        <Route path="chat/private/:chatWorkflowId" element={<BusinessChat />} />
        <Route path="chat/:workflowName" element={<BusinessChat />} />
        <Route path="workflows" element={<Library />} />
        <Route path="library" element={<Library />} />
        <Route path="builder/:name" element={<Builder />} />
        <Route path="builder" element={<Builder />} />
        <Route path="business/:runId" element={<BusinessView />} />
        <Route path="business-chat" element={<BusinessChat />} />
        <Route path="business-chat/:workflowName" element={<BusinessChat />} />
        <Route path="guided/:runId" element={<GuidedRunRedirect />} />
        <Route path="cockpit/:runId" element={<Cockpit />} />
        <Route path="history" element={<RunHistory />} />
        <Route path="history/:runId" element={<RunHistory />} />
        <Route path="workflow-runs" element={<RunHistory />} />
        <Route path="workflow-runs/:runId" element={<RunHistory />} />
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
