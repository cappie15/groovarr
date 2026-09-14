import { Routes, Route } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
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
import Connect from './pages/Connect';
import System from './pages/System';
import NotFound from './pages/NotFound';

export default function App() {
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
          <Route path="/connect" element={<Connect />} />
          <Route path="/system" element={<System />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
