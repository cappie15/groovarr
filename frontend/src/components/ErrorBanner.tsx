import './ErrorBanner.css';

/**
 * Persistent, page-level error state (as opposed to `Toast`, which is for
 * transient notices) — used when a page's primary data failed to load, so
 * the message stays visible until the user retries rather than fading out.
 */
export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
