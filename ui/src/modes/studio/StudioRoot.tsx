import { Routes, Route, Navigate } from 'react-router-dom';
import { StudioLayout } from './StudioLayout';
import { Library } from './Library';
import { BuilderStub } from './BuilderStub';
import { CockpitStub } from './CockpitStub';

export function StudioRoot() {
  return (
    <Routes>
      <Route element={<StudioLayout />}>
        <Route index element={<Navigate to="library" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="builder/:name" element={<BuilderStub />} />
        <Route path="builder" element={<BuilderStub />} />
        <Route path="cockpit/:runId" element={<CockpitStub />} />
      </Route>
    </Routes>
  );
}