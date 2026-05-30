import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ModeShell } from './components/ModeShell';
import { StudioRoot } from './modes/studio/StudioRoot';
import { EvalRoot } from './modes/eval/EvalRoot';
import { OperatorRoot } from './modes/operator/OperatorRoot';

export default function App() {
  return (
    <BrowserRouter>
      <ModeShell>
        <Routes>
          <Route path="/" element={<Navigate to="/studio" replace />} />
          <Route path="/studio/*" element={<StudioRoot />} />
          <Route path="/eval/*"   element={<EvalRoot />} />
          <Route path="/operator/*" element={<OperatorRoot />} />
        </Routes>
      </ModeShell>
    </BrowserRouter>
  );
}