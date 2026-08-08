import { useState, type FormEvent } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ApiError, getSeries } from '../api';
import type { SeriesResult } from '../types';
import ErrorBanner from './ErrorBanner';

export default function SeriesTab() {
  const [sourceId, setSourceId] = useState('WB_WDI');
  const [indicatorId, setIndicatorId] = useState('SP_POP_TOTL');
  const [refArea, setRefArea] = useState('AFG');
  const [startPeriod, setStartPeriod] = useState('');
  const [endPeriod, setEndPeriod] = useState('');
  const [result, setResult] = useState<SeriesResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const data = await getSeries({
        sourceId,
        indicatorId,
        refArea,
        startPeriod: startPeriod || undefined,
        endPeriod: endPeriod || undefined,
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unexpected error.');
    } finally {
      setLoading(false);
    }
  }

  const chartData = result?.observations.map((o) => ({ period: o.period, value: o.value })) ?? [];

  return (
    <section>
      <form onSubmit={onSubmit} className="form-grid">
        <label>
          Источник
          <input value={sourceId} onChange={(e) => setSourceId(e.target.value)} required />
        </label>
        <label>
          Индикатор
          <input value={indicatorId} onChange={(e) => setIndicatorId(e.target.value)} required />
        </label>
        <label>
          Регион/страна
          <input value={refArea} onChange={(e) => setRefArea(e.target.value)} required />
        </label>
        <label>
          С периода
          <input value={startPeriod} onChange={(e) => setStartPeriod(e.target.value)} placeholder="2015" />
        </label>
        <label>
          По период
          <input value={endPeriod} onChange={(e) => setEndPeriod(e.target.value)} placeholder="2020" />
        </label>
        <button type="submit" disabled={loading}>
          {loading ? 'Загружаю…' : 'Получить ряд'}
        </button>
      </form>

      {error && <ErrorBanner message={error} />}

      {result && (
        <>
          <p className="attribution">
            Источник: {result.attribution.source_name} ({result.attribution.dataset_id}), получено{' '}
            {new Date(result.attribution.retrieved_at).toLocaleString()}
            {result.attribution.source_url && (
              <>
                {' — '}
                <a href={result.attribution.source_url} target="_blank" rel="noreferrer">
                  первоисточник
                </a>
              </>
            )}
          </p>

          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" />
              <YAxis />
              <Tooltip />
              <Line type="monotone" dataKey="value" stroke="#2563eb" connectNulls />
            </LineChart>
          </ResponsiveContainer>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Период</th>
                  <th>Значение</th>
                </tr>
              </thead>
              <tbody>
                {result.observations.map((o) => (
                  <tr key={o.period}>
                    <td>{o.period}</td>
                    <td>{o.value ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
