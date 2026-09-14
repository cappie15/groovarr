import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type PlaylistOut = components['schemas']['PlaylistOut'];
export type ExternalPlaylistOut = components['schemas']['ExternalPlaylistOut'];
export type ExternalPlatform = components['schemas']['ExternalPlatform'];
type ExternalPlaylistConfigRequest = components['schemas']['ExternalPlaylistConfigRequest'];

export interface ListPlaylistsParams {
  limit?: number;
  offset?: number;
}

/** GET /api/playlists */
export function listPlaylists(params: ListPlaylistsParams = {}, options?: RequestOptions): Promise<PlaylistOut[]> {
  return apiClient.get<PlaylistOut[]>('/playlists', { ...options, query: { ...params, ...options?.query } });
}

/** POST /api/playlists — Client Credentials mode: any public/unlisted Spotify
 * playlist URL or bare ID, no login step required. */
export function connectPlaylist(urlOrId: string, options?: RequestOptions): Promise<PlaylistOut> {
  return apiClient.post<PlaylistOut>('/playlists', { url_or_id: urlOrId }, options);
}

/** POST /api/playlists/{spotify_id}/sync */
export function syncPlaylistNow(spotifyId: string, options?: RequestOptions): Promise<PlaylistOut> {
  return apiClient.post<PlaylistOut>(`/playlists/${encodeURIComponent(spotifyId)}/sync`, undefined, options);
}

/** POST /api/playlists/{spotify_id}/disconnect — does NOT delete media or
 * empty any external playlist (§10); only stops future Spotify-driven
 * changes. */
export function disconnectPlaylist(spotifyId: string, options?: RequestOptions): Promise<PlaylistOut> {
  return apiClient.post<PlaylistOut>(`/playlists/${encodeURIComponent(spotifyId)}/disconnect`, undefined, options);
}

/** PUT /api/playlists/{spotify_id}/external-config — §55/§58 per-playlist
 * Jellyfin/Plex generation toggles + destination-name overrides. */
export function updateExternalConfig(
  spotifyId: string,
  body: ExternalPlaylistConfigRequest,
  options?: RequestOptions,
): Promise<PlaylistOut> {
  return apiClient.put<PlaylistOut>(`/playlists/${encodeURIComponent(spotifyId)}/external-config`, body, options);
}

/** GET /api/playlists/{spotify_id}/external — current Jellyfin/Plex sync
 * status for this playlist (one row per enabled platform). */
export function getExternalStatus(spotifyId: string, options?: RequestOptions): Promise<ExternalPlaylistOut[]> {
  return apiClient.get<ExternalPlaylistOut[]>(`/playlists/${encodeURIComponent(spotifyId)}/external`, options);
}

/** POST /api/playlists/{spotify_id}/sync-external — rebuild the Jellyfin/Plex
 * playlist right now, without waiting for the next Spotify/acquisition
 * trigger. */
export function syncExternalPlaylist(spotifyId: string, options?: RequestOptions): Promise<ExternalPlaylistOut[]> {
  return apiClient.post<ExternalPlaylistOut[]>(
    `/playlists/${encodeURIComponent(spotifyId)}/sync-external`,
    undefined,
    options,
  );
}

/** POST /api/playlists/{spotify_id}/external/{platform}/resolve — §59: a
 * same-named, Groovarr-unmanaged playlist already exists on that server.
 * "adopt" takes ownership of it; "cancel" forgets the conflict so a
 * different destination name (set via updateExternalConfig) can be tried. */
export function resolveCollision(
  spotifyId: string,
  platform: ExternalPlatform,
  action: 'adopt' | 'cancel',
  options?: RequestOptions,
): Promise<ExternalPlaylistOut> {
  return apiClient.post<ExternalPlaylistOut>(
    `/playlists/${encodeURIComponent(spotifyId)}/external/${platform}/resolve`,
    { action },
    options,
  );
}
