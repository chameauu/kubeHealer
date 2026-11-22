import type { ReactNode } from 'react';

export interface StubSection {
  title: string;
  body: string;
}

/**
 * The only thing a stub may render: what the page will hold, and that it is
 * not wired to a service in this build. No sample topology, no invented counts.
 */
export function StubPage({
  title,
  lead,
  sections,
  note,
  aside,
}: {
  title: string;
  lead: string;
  sections: StubSection[];
  note: string;
  aside?: ReactNode;
}) {
  return (
    <div className="stub-page">
      <div className="stub-page__inner">
        <p className="eyebrow">not implemented in this build</p>
        <h1 className="stub-page__title">{title}</h1>
        <p className="stub-page__lead">{lead}</p>

        <div className="stub-grid">
          {sections.map((section) => (
            <section className="stub-block" key={section.title}>
              <h2>{section.title}</h2>
              <p>{section.body}</p>
            </section>
          ))}
        </div>

        <div className="stub-note">
          <span className="stub-note__tag">disconnected</span>
          <p>{note}</p>
          {aside}
        </div>
      </div>
    </div>
  );
}
