import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type HistoryEventOut = components['schemas']['HistoryEventOut'];

export interface ListHistoryParams {
  track_id?: number;
  media_asset_id?: number;
  limit?: number;
  offset?: number;
}

/** GET /api/history — "what happened to this track" (§65): a read-only,
 * append-only event log written by other services' own lifecycle points. */
export function listHistory(params: ListHistoryParams = {}, options?: RequestOptions): Promise<HistoryEventOut[]> {
  return apiClient.get<HistoryEventOut[]>('/history', { ...options, query: { ...params, ...options?.query } });
}
