import { apiClient, type RequestOptions } from './client';
import type { components } from './schema';

export type NotificationProvider = components['schemas']['NotificationProvider'];
export type NotificationEventTypeOut = components['schemas']['NotificationEventTypeOut'];
export type NotificationConnectionOut = components['schemas']['NotificationConnectionOut'];
export type NotificationConnectionCreateRequest = components['schemas']['NotificationConnectionCreateRequest'];
export type NotificationConnectionUpdateRequest = components['schemas']['NotificationConnectionUpdateRequest'];

/** GET /api/notifications/event-types — every subscribable event type, the
 * real vocabulary `app.services.history.record_event` already uses (not a
 * parallel taxonomy invented for Connect). Powers the per-connection event
 * checkboxes below. */
export function listNotificationEventTypes(options?: RequestOptions): Promise<NotificationEventTypeOut[]> {
  return apiClient.get<NotificationEventTypeOut[]>('/notifications/event-types', options);
}

/** GET /api/notifications */
export function listNotificationConnections(options?: RequestOptions): Promise<NotificationConnectionOut[]> {
  return apiClient.get<NotificationConnectionOut[]>('/notifications', options);
}

/** POST /api/notifications */
export function createNotificationConnection(
  body: NotificationConnectionCreateRequest,
  options?: RequestOptions,
): Promise<NotificationConnectionOut> {
  return apiClient.post<NotificationConnectionOut>('/notifications', body, options);
}

/** PUT /api/notifications/{id} */
export function updateNotificationConnection(
  id: number,
  body: NotificationConnectionUpdateRequest,
  options?: RequestOptions,
): Promise<NotificationConnectionOut> {
  return apiClient.put<NotificationConnectionOut>(`/notifications/${id}`, body, options);
}

/** DELETE /api/notifications/{id} */
export function deleteNotificationConnection(id: number, options?: RequestOptions): Promise<void> {
  return apiClient.delete<void>(`/notifications/${id}`, options);
}

/** POST /api/notifications/{id}/test — re-tests the connection's underlying
 * already-configured Jellyfin/Plex server (see app/api/settings.ts's
 * Jellyfin/Plex sections for configuring the server itself). */
export function testNotificationConnection(id: number, options?: RequestOptions): Promise<{ ok: boolean }> {
  return apiClient.post<{ ok: boolean }>(`/notifications/${id}/test`, undefined, options);
}
