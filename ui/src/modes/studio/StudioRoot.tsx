import { Routes, Route, Navigate } from 'react-router-dom';
import { StudioLayout } from './StudioLayout';
import { Library } from './Library';
import { Builder } from './Builder';                  
import { Cockpit } from './Cockpit';

export function StudioRoot() {
  return (
    <Routes>
      <Route element={<StudioLayout />}>
        <Route index element={<Navigate to="library" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="builder/:name" element={<Builder />} />     
        <Route path="builder" element={<Builder />} />            
        <Route path="cockpit/:runId" element={<Cockpit />} />
      </Route>
    </Routes>
  );
}
