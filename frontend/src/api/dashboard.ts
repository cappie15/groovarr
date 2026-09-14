import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type DashboardSummary = components['schemas']['DashboardSummary'];

/** GET /api/dashboard/summary — aggregate counts for the Dashboard page (§63). */
export function getDashboardSummary(options?: RequestOptions): Promise<DashboardSummary> {
  return apiClient.get<DashboardSummary>('/dashboard/summary', options);
}
