import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/tokens.css';
import './styles/global.css';
import './styles/projects.css';
import './styles/tags-home.css';
import './styles/agent-view.css';
import './styles/theme-dark.css';
import { App } from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { onThemeChange, readTheme, setThemeAttribute, type ThemeName } from './lib/theme';

setThemeAttribute(readTheme());

function Root() {
  const [theme, setTheme] = useState<ThemeName>(() => readTheme());
  useEffect(() => onThemeChange(setTheme), []);
  return <App key={theme} />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </StrictMode>,
);
