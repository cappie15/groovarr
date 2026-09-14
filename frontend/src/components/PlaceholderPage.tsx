interface PlaceholderPageProps {
  title: string;
  description: string;
}

/**
 * Phase 1 (Foundation) placeholder: every routed section renders one of
 * these until its real view is built in a later phase. Deliberately plain —
 * a heading and a one-line description of what will eventually live here.
 */
export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <section className="placeholder-page">
      <h1>{title}</h1>
      <p className="placeholder-page__description">{description}</p>
      <div className="placeholder-page__badge">Not yet implemented — Phase 1 scaffold</div>
    </section>
  );
}
