import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type QueueItemOut = components['schemas']['QueueItemOut'];
export type DownloadAttemptOut = components['schemas']['DownloadAttemptOut'];

export interface ListQueueParams {
  limit?: number;
  offset?: number;
}

/** GET /api/queue — every MediaAsset currently in an active acquisition
 * state (CandidateSelected/Queued/Downloading/Processing/Importing), each
 * with its full DownloadAttempt history (§64/§76). */
export function listQueue(params: ListQueueParams = {}, options?: RequestOptions): Promise<QueueItemOut[]> {
  return apiClient.get<QueueItemOut[]>('/queue', { ...options, query: { ...params, ...options?.query } });
}

/** POST /api/queue/{asset_id}/retry — manual Retry for a DOWNLOAD_FAILED
 * asset; resets the attempt/backoff cycle (§50). */
export function retryDownload(assetId: number, options?: RequestOptions): Promise<QueueItemOut> {
  return apiClient.post<QueueItemOut>(`/queue/${assetId}/retry`, undefined, options);
}

/** POST /api/queue/{asset_id}/cancel — safe Cancel for a not-yet-started
 * (QUEUED) asset. */
export function cancelQueued(assetId: number, options?: RequestOptions): Promise<QueueItemOut> {
  return apiClient.post<QueueItemOut>(`/queue/${assetId}/cancel`, undefined, options);
}
