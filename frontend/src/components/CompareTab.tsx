import { useState, type FormEvent } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ApiError, compare } from '../api';
import type { ComparisonTable } from '../types';
import ErrorBanner from './ErrorBanner';

type Mode = 'countries' | 'indicators';

const CHART_COLORS = ['#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed', '#0891b2'];

function splitList(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function CompareTab() {
  const [mode, setMode] = useState<Mode>('countries');
  const [sourceId, setSourceId] = useState('WB_WDI');
  const [indicatorId, setIndicatorId] = useState('SP_POP_TOTL');
  const [refAreas, setRefAreas] = useState('AFG, USA, LUX');
  const [indicatorIds, setIndicatorIds] = useState('');
  const [refArea, setRefArea] = useState('AFG');
  const [startPeriod, setStartPeriod] = useState('');
  const [endPeriod, setEndPeriod] = useState('');
  const [growth, setGrowth] = useState(false);
  const [rank, setRank] = useState(false);
  const [ratioTo, setRatioTo] = useState('');
  const [table, setTable] = useState<ComparisonTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setTable(null);
    setLoading(true);
    try {
      const data = await compare({
        source_id: sourceId,
        indicator_id: mode === 'countries' ? indicatorId : undefined,
        ref_areas: mode === 'countries' ? splitList(refAreas) : undefined,
        indicator_ids: mode === 'indicators' ? splitList(indicatorIds) : undefined,
        ref_area: mode === 'indicators' ? refArea : undefined,
        start_period: startPeriod || undefined,
        end_period: endPeriod || undefined,
        growth,
        rank,
        ratio_to: ratioTo || undefined,
      });
      setTable(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unexpected error.');
    } finally {
      setLoading(false);
    }
  }

  const baseColumns = table?.columns.filter((c) => !c.derived) ?? [];

  return (
    <section>
      <div className="mode-toggle">
        <label>
          <input
            type="radio"
            checked={mode === 'countries'}
            onChange={() => setMode('countries')}
          />
          Межстрановое (один индикатор, несколько стран)
        </label>
        <label>
          <input
            type="radio"
            checked={mode === 'indicators'}
            onChange={() => setMode('indicators')}
          />
          Межиндикаторное (несколько индикаторов, одна страна)
        </label>
      </div>

      <form onSubmit={onSubmit} className="form-grid">
        <label>
          Источник
          <input value={sourceId} onChange={(e) => setSourceId(e.target.value)} required />
        </label>

        {mode === 'countries' ? (
          <>
            <label>
              Индикатор
              <input value={indicatorId} onChange={(e) => setIndicatorId(e.target.value)} required />
            </label>
            <label>
              Регионы (через запятую)
              <input value={refAreas} onChange={(e) => setRefAreas(e.target.value)} required />
            </label>
          </>
        ) : (
          <>
            <label>
              Индикаторы (через запятую)
              <input value={indicatorIds} onChange={(e) => setIndicatorIds(e.target.value)} required />
            </label>
            <label>
              Регион
              <input value={refArea} onChange={(e) => setRefArea(e.target.value)} required />
            </label>
          </>
        )}

        <label>
          С периода
          <input value={startPeriod} onChange={(e) => setStartPeriod(e.target.value)} placeholder="2015" />
        </label>
        <label>
          По период
          <input value={endPeriod} onChange={(e) => setEndPeriod(e.target.value)} placeholder="2020" />
        </label>

        <label className="checkbox">
          <input type="checkbox" checked={growth} onChange={(e) => setGrowth(e.target.checked)} />
          Темп роста год-к-году
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={rank} onChange={(e) => setRank(e.target.checked)} />
          Ранг по периоду
        </label>
        <label>
          Отношение к колонке (ключ)
          <input value={ratioTo} onChange={(e) => setRatioTo(e.target.value)} placeholder="например, USA" />
        </label>

        <button type="submit" disabled={loading}>
          {loading ? 'Загружаю…' : 'Сравнить'}
        </button>
      </form>

      {error && <ErrorBanner message={error} />}

      {table && (
        <>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={table.rows}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" />
              <YAxis />
              <Tooltip />
              <Legend />
              {baseColumns.map((col, i) => (
                <Line
                  key={col.key}
                  type="monotone"
                  dataKey={col.key}
                  name={col.label}
                  stroke={CHART_COLORS[i % CHART_COLORS.length]}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Период</th>
                  {table.columns.map((col) => (
                    <th
                      key={col.key}
                      title={col.attribution ? col.attribution.source_name : 'вычислено, не из источника'}
                    >
                      {col.label}
                      {col.derived ? ' *' : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    {table.columns.map((col) => (
                      <td key={col.key}>{row[col.key] ?? '—'}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">* — вычисляемая колонка (не получена из источника напрямую)</p>
        </>
      )}
    </section>
  );
}
