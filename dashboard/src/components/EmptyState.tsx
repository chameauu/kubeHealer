import type { ReactNode } from 'react';

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children ? <p className="empty__body">{children}</p> : null}
    </div>
  );
}

export function LoadingLine({ label = 'loading' }: { label?: string }) {
  return <p className="loading-line">{label}…</p>;
}
