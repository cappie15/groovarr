import { NavLink } from 'react-router-dom';
import { getHealth } from '../api/client';
import { navGroups } from '../nav';
import { useApiQuery } from '../hooks/useApiQuery';
import { BrandMark } from './BrandMark';
import { Icon } from './Icon';

export function Sidebar() {
  // Real backend status/version in the footer, replacing the Phase 1
  // scaffold's hardcoded "Phase 1 · foundation" label — this is shared
  // chrome rendered on every page, not one of the pages this pass wires up,
  // but leaving a now-false status string in permanently-visible UI would
  // be a worse outcome than a two-line fix.
  const { data: health } = useApiQuery((signal) => getHealth({ signal }));
  const healthy = health?.status === 'ok' && health.db === 'ok';

  return (
    <nav className="sidebar" aria-label="Main navigation">
      <div className="sidebar__brand">
        <BrandMark />
        <span className="sidebar__brand-name">Groovarr</span>
      </div>

      <div className="sidebar__scroll">
        {navGroups.map((group) => (
          <div className="sidebar__group" key={group.label}>
            <div className="sidebar__group-label">{group.label}</div>
            <ul className="sidebar__list">
              {group.items.map((item) => (
                <li key={item.path}>
                  <NavLink
                    to={item.path}
                    end={item.path === '/'}
                    className={({ isActive }) =>
                      'sidebar__link' + (isActive ? ' sidebar__link--active' : '')
                    }
                  >
                    <Icon name={item.icon} />
                    <span>{item.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="sidebar__footer">
        <span
          className="sidebar__footer-status"
          aria-hidden="true"
          style={{ background: health ? (healthy ? undefined : 'var(--danger)') : '#6b7280' }}
        />
        <span>{health ? `v${health.version}` : 'Connecting…'}</span>
      </div>
    </nav>
  );
}
