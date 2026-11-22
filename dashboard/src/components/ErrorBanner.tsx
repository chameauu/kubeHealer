import { errorLabel, type ApiError } from '../api/client';

export function ErrorBanner({
  error,
  title = 'request failed',
  note,
  onRetry,
}: {
  error: ApiError | string;
  title?: string;
  note?: string;
  onRetry?: () => void;
}) {
  const api = typeof error === 'string' ? null : error;
  const detail = typeof error === 'string' ? error : error.detail;
  return (
    <div className="error-banner" role="alert">
      <div className="error-banner__head">
        <span className="error-banner__code">{api ? errorLabel(api) : 'error'}</span>
        <span className="error-banner__title">{title}</span>
        {onRetry ? (
          <button type="button" className="btn btn--ghost btn--xs" onClick={onRetry}>
            retry
          </button>
        ) : null}
      </div>
      <p className="error-banner__detail">{detail}</p>
      {note ? <p className="error-banner__note">{note}</p> : null}
    </div>
  );
}
