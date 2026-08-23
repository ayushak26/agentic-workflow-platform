import { lazy } from 'react';
import { Routes, Route, Navigate, useParams } from 'react-router-dom';
import { StudioLayout } from './StudioLayout';

const Library = lazy(() => import('./Library').then(module => ({ default: module.Library })));
const Builder = lazy(() => import('./Builder').then(module => ({ default: module.Builder })));
const Cockpit = lazy(() => import('./Cockpit').then(module => ({ default: module.Cockpit })));
const BusinessChat = lazy(() => import('./business-chat/BusinessChat').then(module => ({ default: module.BusinessChat })));
const RunHistory = lazy(() => import('./RunHistory').then(module => ({ default: module.RunHistory })));
const RunCandidates = lazy(() => import('./RunCandidates').then(module => ({ default: module.RunCandidates })));
const ProposalReview = lazy(() => import('./ProposalReview').then(module => ({ default: module.ProposalReview })));

function LegacyRunRedirect() {
  const { runId } = useParams();
  return <Navigate to={`/workflow-runs/${runId}`} replace />;
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
        <Route path="builder/:name" element={<Builder />} />
        <Route path="builder" element={<Builder />} />
        <Route path="workflow-runs" element={<RunHistory />} />
        <Route path="workflow-runs/:runId" element={<RunHistory />} />
        <Route path="cockpit/:runId" element={<Cockpit />} />
        <Route path="candidates/:runId" element={<RunCandidates />} />
        <Route path="proposal-review" element={<ProposalReview />} />
        <Route path="proposal-review/:runId" element={<ProposalReview />} />
        <Route path="guided/:runId" element={<LegacyRunRedirect />} />
        <Route path="history" element={<Navigate to="/workflow-runs" replace />} />
        <Route path="history/:runId" element={<LegacyRunRedirect />} />
        <Route path="library" element={<Navigate to="/workflows" replace />} />
        <Route path="business-chat/*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}
