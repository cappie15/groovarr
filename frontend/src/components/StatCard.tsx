import type { ReactNode } from 'react';
import './StatCard.css';

/**
 * A single operationally-useful number for the Dashboard (§63) — deliberately
 * plain (label + big number + optional sub-line), not a chart. The dashboard
 * is meant to answer "what needs my attention", not look like an analytics
 * product.
 */
export function StatCard({
  label,
  value,
  tone = 'neutral',
  sub,
}: {
  label: string;
  value: ReactNode;
  tone?: 'neutral' | 'info' | 'warning' | 'danger' | 'success';
  sub?: ReactNode;
}) {
  return (
    <div className={`stat-card stat-card--${tone}`}>
      <div className="stat-card__label">{label}</div>
      <div className="stat-card__value">{value}</div>
      {sub && <div className="stat-card__sub">{sub}</div>}
    </div>
  );
}
