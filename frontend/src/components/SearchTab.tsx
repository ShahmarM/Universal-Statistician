import { useState, type FormEvent } from 'react';
import { ApiError, searchIndicator } from '../api';
import type { IndicatorMeta } from '../types';
import ErrorBanner from './ErrorBanner';

export default function SearchTab() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<IndicatorMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await searchIndicator(query);
      setResults(data);
      setSearched(true);
    } catch (err) {
      setResults([]);
      setError(err instanceof ApiError ? err.message : 'Unexpected error.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <p className="hint">
        Полнотекстовый поиск по каталогу индикаторов, на любом проиндексированном языке
        (например «population» или «produit»).
      </p>
      <form onSubmit={onSubmit} className="form-row">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="population, produit, gdp…"
          aria-label="Search query"
        />
        <button type="submit" disabled={loading || !query.trim()}>
          {loading ? 'Ищу…' : 'Искать'}
        </button>
      </form>

      {error && <ErrorBanner message={error} />}

      {!error && searched && results.length === 0 && (
        <p className="hint">Ничего не найдено.</p>
      )}

      <ul className="results">
        {results.map((r) => (
          <li key={`${r.source_id}:${r.indicator_id}`}>
            <strong>{r.name}</strong>
            <span className="meta">
              {' '}
              — {r.source_id} / {r.indicator_id}
            </span>
            {r.description && <p className="description">{r.description}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
