import { useEffect } from 'react';
import { Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { getSettings, type SettingsOut } from './api/settings';
import { Sidebar } from './components/Sidebar';
import { useApiQuery } from './hooks/useApiQuery';
import { isSetupWizardDismissed } from './lib/onboarding';
import Dashboard from './pages/Dashboard';
import Playlists from './pages/Playlists';
import Tracks from './pages/Tracks';
import Wanted from './pages/Wanted';
import Incomplete from './pages/Incomplete';
import AutomaticSearch from './pages/AutomaticSearch';
import ManualSearch from './pages/ManualSearch';
import Activity from './pages/Activity';
import History from './pages/History';
import Settings from './pages/Settings';
import SetupWizard from './pages/SetupWizard';
import Connect from './pages/Connect';
import System from './pages/System';
import NotFound from './pages/NotFound';

/**
 * Auto-triggers the Setup Wizard on a fresh install: no Spotify Client
 * Credentials configured yet (the same "has anyone touched this install at
 * all" signal a brand-new operator's Settings page would otherwise greet
 * them with, unassisted) and this browser hasn't already dismissed/finished
 * it. Runs once per settings load rather than per render — it only ever
 * *redirects into* /setup, never away from wherever the operator already
 * navigated to (visiting /setup directly, e.g. via Settings → General's
 * "Run the Setup Wizard" link, always works regardless of this check).
 */
function useSetupWizardRedirect(settings: SettingsOut | null) {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    if (!settings) return;
    if (settings.spotify_client_configured) return;
    if (isSetupWizardDismissed()) return;
    if (location.pathname === '/setup') return;
    navigate('/setup', { replace: true });
  }, [settings, location.pathname, navigate]);
}

export default function App() {
  const { data: settings } = useApiQuery((signal) => getSettings({ signal }));
  useSetupWizardRedirect(settings);

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/playlists" element={<Playlists />} />
          <Route path="/tracks" element={<Tracks />} />
          <Route path="/wanted" element={<Wanted />} />
          <Route path="/incomplete" element={<Incomplete />} />
          <Route path="/search/automatic" element={<AutomaticSearch />} />
          <Route path="/search/manual" element={<ManualSearch />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/setup" element={<SetupWizard />} />
          <Route path="/connect" element={<Connect />} />
          <Route path="/system" element={<System />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
