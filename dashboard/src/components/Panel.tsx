import type { ReactNode } from 'react';

export function Panel({
  title,
  meta,
  actions,
  children,
  className = '',
  bodyClassName = '',
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`panel ${className}`.trim()}>
      <header className="panel__head">
        <h2 className="panel__title">{title}</h2>
        {meta ? <div className="panel__meta">{meta}</div> : null}
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className={`panel__body ${bodyClassName}`.trim()}>{children}</div>
    </section>
  );
}

export interface FieldRow {
  label: ReactNode;
  value: ReactNode;
  mono?: boolean;
  wide?: boolean;
}

/** A definition grid for detail records; tabular, not card-per-field. */
export function Fields({ rows }: { rows: FieldRow[] }) {
  return (
    <dl className="fields">
      {rows.map((row, index) => (
        <div className={`fields__row${row.wide ? ' fields__row--wide' : ''}`} key={index}>
          <dt className="fields__label">{row.label}</dt>
          <dd className={`fields__value${row.mono ? ' mono' : ''}`}>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
