import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type WantedTrackOut = components['schemas']['WantedTrackOut'];
export type VideoCandidateOut = components['schemas']['VideoCandidateOut'];
export type SearchOutcomeOut = components['schemas']['SearchOutcomeOut'];

/** The real shape behind `VideoCandidateOut.score_breakdown` — the backend
 * exposes it as a plain `list` (untyped in the OpenAPI schema), but every
 * entry app.matching.scoring actually writes is one of these (§21). */
export interface ScoreBreakdownEntry {
  signal: string;
  delta: number;
  explanation: string;
}

export interface ListWantedParams {
  limit?: number;
  offset?: number;
}

/** GET /api/search/wanted */
export function listWantedTracks(params: ListWantedParams = {}, options?: RequestOptions): Promise<WantedTrackOut[]> {
  return apiClient.get<WantedTrackOut[]>('/search/wanted', { ...options, query: { ...params, ...options?.query } });
}

/** POST /api/search/run — bulk Automatic Search over every wanted track. */
export function runSearchForAllWanted(options?: RequestOptions): Promise<SearchOutcomeOut[]> {
  return apiClient.post<SearchOutcomeOut[]>('/search/run', undefined, options);
}

/** POST /api/search/tracks/{track_id}/run — Automatic Search for one track. */
export function runSearchForTrack(trackId: number, options?: RequestOptions): Promise<SearchOutcomeOut> {
  return apiClient.post<SearchOutcomeOut>(`/search/tracks/${trackId}/run`, undefined, options);
}

/** POST /api/search/tracks/{track_id}/search-again — reopen an already-
 * classified or manually-selected track (§34/§37); always available
 * regardless of the "Monitor for Better Versions" setting. */
export function searchAgain(trackId: number, options?: RequestOptions): Promise<SearchOutcomeOut> {
  return apiClient.post<SearchOutcomeOut>(`/search/tracks/${trackId}/search-again`, undefined, options);
}

/** GET /api/search/tracks/{track_id}/candidates */
export function getCandidatesForTrack(trackId: number, options?: RequestOptions): Promise<VideoCandidateOut[]> {
  return apiClient.get<VideoCandidateOut[]>(`/search/tracks/${trackId}/candidates`, options);
}

/** POST /api/search/tracks/{track_id}/select — Manual Selection for a track
 * with no file yet (§33). For a track that already has a working file, use
 * `replaceTrackMedia` instead (§34) — the backend rejects a mismatched call
 * with a 422 either way, but the UI should pick the right one deliberately. */
export function selectCandidate(
  trackId: number,
  videoCandidateId: number,
  options?: RequestOptions,
): Promise<SearchOutcomeOut> {
  return apiClient.post<SearchOutcomeOut>(
    `/search/tracks/${trackId}/select`,
    { video_candidate_id: videoCandidateId },
    options,
  );
}

/** POST /api/search/tracks/{track_id}/replace — safely swap an already-
 * downloaded track's file for a different candidate (§34): the old file
 * keeps serving until the new one is fully downloaded, tagged, and
 * validated. */
export function replaceTrackMedia(
  trackId: number,
  videoCandidateId: number,
  options?: RequestOptions,
): Promise<SearchOutcomeOut> {
  return apiClient.post<SearchOutcomeOut>(
    `/search/tracks/${trackId}/replace`,
    { video_candidate_id: videoCandidateId },
    options,
  );
}
