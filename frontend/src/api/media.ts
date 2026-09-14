import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type MediaAssetOut = components['schemas']['MediaAssetOut'];
export type MediaState = components['schemas']['MediaState'];
export type LibraryScanResultOut = components['schemas']['LibraryScanResultOut'];

export interface ListMediaAssetsParams {
  state?: MediaState;
  /** Matches a MediaAsset whose `track_id` OR `candidate_track_id` equals
   * this — a ManualReviewRequired row only has the latter set (§7). */
  for_track?: number;
  limit?: number;
  offset?: number;
}

/** GET /api/media */
export function listMediaAssets(
  params: ListMediaAssetsParams = {},
  options?: RequestOptions,
): Promise<MediaAssetOut[]> {
  return apiClient.get<MediaAssetOut[]>('/media', { ...options, query: { ...params, ...options?.query } });
}

/** POST /api/media/scan — §15 existing-library import: matches files already
 * on disk against known tracks; never deletes/renames/overwrites anything
 * automatically. */
export function triggerLibraryScan(options?: RequestOptions): Promise<LibraryScanResultOut> {
  return apiClient.post<LibraryScanResultOut>('/media/scan', undefined, options);
}

/** POST /api/media/{asset_id}/organize — explicit, user-triggered rename to
 * match the configured naming template (§15/§16) — never automatic. */
export function organizeMediaAsset(assetId: number, options?: RequestOptions): Promise<MediaAssetOut> {
  return apiClient.post<MediaAssetOut>(`/media/${assetId}/organize`, undefined, options);
}

/** The MediaAsset "belonging to" a track, if any — there's at most one
 * per track in practice (see `for_track`'s either-column semantics above). */
export async function getMediaAssetForTrack(
  trackId: number,
  options?: RequestOptions,
): Promise<MediaAssetOut | null> {
  const rows = await listMediaAssets({ for_track: trackId, limit: 1 }, options);
  return rows[0] ?? null;
}
