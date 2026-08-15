import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
// Loaded before index.css so Tailwind's utility classes (e.g. w-40, flex-1)
// win cascade ties against plain component classes like .builder-field —
// equal-specificity rules resolve to whichever stylesheet loads last.
import './styles/globals.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)