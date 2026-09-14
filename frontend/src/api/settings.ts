import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type SettingsOut = components['schemas']['SettingsOut'];
export type SpotifyCredentialsRequest = components['schemas']['SpotifyCredentialsRequest'];
export type JellyfinConfigRequest = components['schemas']['JellyfinConfigRequest'];
export type PlexConfigRequest = components['schemas']['PlexConfigRequest'];
export type JellyfinUserOut = components['schemas']['JellyfinUserOut'];
export type PlexLibrarySectionOut = components['schemas']['PlexLibrarySectionOut'];

/** GET /api/settings */
export function getSettings(options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.get<SettingsOut>('/settings', options);
}

/** PUT /api/settings/sync-interval */
export function updateSyncInterval(hours: number, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/sync-interval', { default_sync_interval_hours: hours }, options);
}

/** PUT /api/settings/spotify-credentials — the DEFAULT Client Credentials
 * Flow (app-only, no login) — sufficient on its own for any public/unlisted
 * playlist (§2-E). */
export function updateSpotifyCredentials(
  body: SpotifyCredentialsRequest,
  options?: RequestOptions,
): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/spotify-credentials', body, options);
}

/** PUT /api/settings/spotify-user-oauth — toggles the OPTIONAL "Connect your
 * Spotify account" PKCE flow, only needed for private/collaborative
 * playlists (§2-E). Turning it off drops any stored refresh token. */
export function updateSpotifyUserOAuthEnabled(enabled: boolean, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/spotify-user-oauth', { enabled }, options);
}

/** GET /api/spotify/oauth/authorize — a real browser navigation (not a
 * fetch), since it 302s to Spotify's own consent page and back to our own
 * /callback route. */
export function spotifyOAuthAuthorizeUrl(): string {
  return '/api/spotify/oauth/authorize';
}

/** PUT /api/settings/jellyfin */
export function updateJellyfinConfig(body: JellyfinConfigRequest, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/jellyfin', body, options);
}

/** PUT /api/settings/plex */
export function updatePlexConfig(body: PlexConfigRequest, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/plex', body, options);
}

/** PUT /api/settings/matching */
export function updateMatchingThreshold(threshold: number, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/matching', { automatic_match_threshold: threshold }, options);
}

/** PUT /api/settings/download */
export function updateDownloadSettings(
  body: { max_concurrent_downloads: number; max_download_attempts: number },
  options?: RequestOptions,
): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/download', body, options);
}

/** PUT /api/settings/lyrics */
export function updateLyricsEnabled(enabled: boolean, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/lyrics', { enabled }, options);
}

/** PUT /api/settings/monitor-better-versions — §36: enabling this may
 * replace an existing music video with a *different* video, not merely a
 * higher-resolution copy — always render `SettingsOut.monitor_better_versions_warning`
 * prominently alongside this control, never soften/omit it. */
export function updateMonitorBetterVersions(enabled: boolean, options?: RequestOptions): Promise<SettingsOut> {
  return apiClient.put<SettingsOut>('/settings/monitor-better-versions', { enabled }, options);
}

/** POST /api/jellyfin/test */
export function testJellyfinConnection(options?: RequestOptions): Promise<{ ok: boolean }> {
  return apiClient.post<{ ok: boolean }>('/jellyfin/test', undefined, options);
}

/** GET /api/jellyfin/users — populates the required "Jellyfin user" picker
 * (§2 row F); only needs url + api key to already be saved. */
export function listJellyfinUsers(options?: RequestOptions): Promise<JellyfinUserOut[]> {
  return apiClient.get<JellyfinUserOut[]>('/jellyfin/users', options);
}

/** POST /api/plex/test */
export function testPlexConnection(options?: RequestOptions): Promise<{ ok: boolean }> {
  return apiClient.post<{ ok: boolean }>('/plex/test', undefined, options);
}

/** GET /api/plex/library-sections — Plex has no "Music Videos" library type
 * (§2 row A); the picker built from this must be understood as "which Music
 * or Other Videos section holds your music videos," not a dedicated type. */
export function listPlexLibrarySections(options?: RequestOptions): Promise<PlexLibrarySectionOut[]> {
  return apiClient.get<PlexLibrarySectionOut[]>('/plex/library-sections', options);
}
