import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'

import App from './App'
import { AppCursor } from './components/app-cursor'
import './index.css'

const container = document.getElementById('root')
if (!container) throw new Error('No #root element to mount into.')

createRoot(container).render(
  <StrictMode>
    <App />
    {/* Outside the app, so it is there on the sign-in page too. */}
    <AppCursor />
  </StrictMode>,
)
