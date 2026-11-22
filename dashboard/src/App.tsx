import { useCallback, useState } from 'react';
import { Agent } from './pages/Agent';
import { ChaosLab } from './pages/ChaosLab';
import { FaultLibrary } from './pages/FaultLibrary';
import { Incidents } from './pages/Incidents';
import { Overview } from './pages/Overview';
import { Topology } from './pages/Topology';
import { Clock } from './components/Clock';

export type View = 'overview' | 'incidents' | 'agent' | 'topology' | 'chaoslab' | 'faultlib';

const NAV: Array<{ id: View; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'incidents', label: 'Incidents' },
  { id: 'agent', label: 'Agent' },
  { id: 'topology', label: 'Topology' },
  { id: 'chaoslab', label: 'Chaos lab' },
  { id: 'faultlib', label: 'Fault library' },
];

export default function App() {
  const [view, setView] = useState<View>('incidents');
  const [agentFocus, setAgentFocus] = useState<string | null>(null);

  const openAgent = useCallback((scenarioId: string) => {
    setAgentFocus(scenarioId);
    setView('agent');
  }, []);

  const consumeFocus = useCallback(() => setAgentFocus(null), []);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        skip to content
      </a>

      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            K
          </span>
          <span className="brand-name">KUBEHEAL</span>
          <span className="brand-sub">console</span>
        </div>

        <nav className="topnav" aria-label="views">
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`topnav__tab${view === item.id ? ' is-active' : ''}`}
              aria-current={view === item.id ? 'page' : undefined}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="topbar__right">
          <span
            className="lab-chip"
            title="Lab deployment behind a port-forward. No authentication, no multi-tenancy."
          >
            lab · no auth
          </span>
          <Clock />
        </div>
      </header>

      <main className="view" id="main">
        {view === 'incidents' ? <Incidents onOpenAgent={openAgent} /> : null}
        {view === 'agent' ? (
          <Agent focusRunId={agentFocus} onFocusConsumed={consumeFocus} />
        ) : null}
        {view === 'overview' ? <Overview /> : null}
        {view === 'topology' ? <Topology /> : null}
        {view === 'chaoslab' ? <ChaosLab /> : null}
        {view === 'faultlib' ? <FaultLibrary /> : null}
      </main>
    </div>
  );
}
