import { Link } from 'react-router-dom';

export default function NotFound() {
  return (
    <section className="placeholder-page">
      <h1>Page not found</h1>
      <p className="placeholder-page__description">
        There's no Groovarr section at this address. <Link to="/">Back to the dashboard</Link>.
      </p>
    </section>
  );
}
