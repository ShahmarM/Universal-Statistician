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
import { ApiError, ask } from '../api';
import type { AskMode, AskResult } from '../types';
import ErrorBanner from './ErrorBanner';

const MODE_LABELS: Record<AskMode, string> = {
  auto: 'Авто',
  fast: 'Быстрый',
  research: 'Исследование',
};

const MODE_HINTS: Record<AskMode, string> = {
  auto: 'Сам решает по тексту вопроса — прямой поиск быстро, сравнение/«почему» — через исследование.',
  fast: 'Один проход: план → поиск → лучший индикатор → данные. Быстро, для прямых вопросов.',
  research: 'Модель сама пошагово ищет, проверяет и сравнивает источники (нужен Claude).',
};

const CHART_COLORS = ['#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed', '#0891b2'];

// Short phrases work with the deterministic fallback planner (no LLM) —
// it treats the whole question as one literal catalog search term, so
// only these match the seeded demo catalog. The longer natural-language
// examples from the task spec are included too, but need "Claude" enabled
// below to be understood — noted explicitly rather than silently failing.
const SIMPLE_EXAMPLES = ['population', 'gross domestic product'];
const NL_EXAMPLES = [
  'What was Azerbaijan’s GDP growth over the last 10 years?',
  'Compare inflation in Turkey, Georgia and Azerbaijan.',
  'Show EU unemployment since 2000.',
  'Which European countries had the highest GDP growth last year?',
];

function ValidationBadge({ status }: { status: 'PASS' | 'WARNING' | 'FAIL' }) {
  return <span className={`validation-badge validation-${status.toLowerCase()}`}>{status}</span>;
}

export default function AskTab() {
  const [question, setQuestion] = useState('');
  const [useLlm, setUseLlm] = useState(false);
  const [mode, setMode] = useState<AskMode>('auto');
  const [debug, setDebug] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(q: string) {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      setResult(await ask(q, { useLlm, mode, debug }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unexpected error.');
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (question.trim()) submit(question.trim());
  }

  function onExampleClick(example: string) {
    setQuestion(example);
    submit(example);
  }

  const baseColumns = result?.table?.columns.filter((c) => !c.derived) ?? [];

  return (
    <section>
      <p className="hint">
        Задайте статистический вопрос на естественном языке. Без ключа Claude
        распознаётся только буквальная поисковая фраза по каталогу (например
        «population»); включите «Использовать Claude» для полноценных вопросов
        (нужен <code>ANTHROPIC_API_KEY</code> на сервере).
      </p>

      <form onSubmit={onSubmit} className="form-row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Задайте статистический вопрос…"
          aria-label="Question"
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading
            ? mode === 'research' || (mode === 'auto' && useLlm)
              ? 'Исследую (поиск → проверка данных → расчёт)…'
              : 'Спрашиваю…'
            : 'Спросить'}
        </button>
      </form>

      <label className="checkbox llm-toggle">
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        Использовать Claude для интерпретации
      </label>

      <fieldset className="mode-selector">
        <legend>Режим</legend>
        {(Object.keys(MODE_LABELS) as AskMode[]).map((m) => (
          <label key={m} className="radio" title={MODE_HINTS[m]}>
            <input
              type="radio"
              name="ask-mode"
              value={m}
              checked={mode === m}
              onChange={() => setMode(m)}
            />
            {MODE_LABELS[m]}
          </label>
        ))}
        <label className="checkbox debug-toggle">
          <input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} />
          Показать ход исследования (debug)
        </label>
      </fieldset>

      <div className="examples">
        <p className="hint">Примеры (без Claude):</p>
        <div className="example-chips">
          {SIMPLE_EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="chip" onClick={() => onExampleClick(ex)}>
              {ex}
            </button>
          ))}
        </div>
        <p className="hint">Примеры (нужен Claude):</p>
        <div className="example-chips">
          {NL_EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="chip" onClick={() => onExampleClick(ex)}>
              {ex}
            </button>
          ))}
        </div>
      </div>

      {error && <ErrorBanner message={error} />}

      {result && (
        <div className="ask-result">
          <p className="mode-used-line">
            Режим: <span className="mode-used-badge">{MODE_LABELS[result.mode_used]}</span>
          </p>

          <p className="answer">{result.answer}</p>

          {result.validation && (
            <p className="validation-line">
              Валидация: <ValidationBadge status={result.validation.status} />
            </p>
          )}

          {result.verification && (
            <p className="validation-line">
              Проверка ответа: <ValidationBadge status={result.verification.status} />
            </p>
          )}

          {result.warnings.length > 0 && (
            <div className="warning-banner">
              {result.warnings.map((w, i) => (
                <p key={i}>{w}</p>
              ))}
            </div>
          )}

          {result.chart && baseColumns.length > 0 && result.table && (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={result.table.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="period" />
                <YAxis />
                <Tooltip />
                <Legend />
                {result.chart.series.map((s, i) => (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stroke={CHART_COLORS[i % CHART_COLORS.length]}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}

          {result.table && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Период</th>
                    {result.table.columns.map((col) => (
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
                  {result.table.rows.map((row) => (
                    <tr key={row.period}>
                      <td>{row.period}</td>
                      {result.table!.columns.map((col) => (
                        <td key={col.key}>{row[col.key] ?? '—'}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="hint">* — вычисляемая колонка (не получена из источника напрямую)</p>
            </div>
          )}

          {result.sources.length > 0 && (
            <div className="sources">
              <h3>Источники</h3>
              <ul>
                {result.sources.map((s) => (
                  <li key={s.source_id}>
                    {s.source_name} ({s.dataset_id})
                    {s.source_url && (
                      <>
                        {' — '}
                        <a href={s.source_url} target="_blank" rel="noreferrer">
                          первоисточник
                        </a>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.query_plan.assumptions.length > 0 && (
            <div className="assumptions">
              <h3>Допущения</h3>
              <ul>
                {result.query_plan.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          )}

          {result.validation && result.validation.findings.length > 0 && (
            <details>
              <summary>Находки валидации ({result.validation.findings.length})</summary>
              <ul>
                {result.validation.findings.map((f, i) => (
                  <li key={i}>
                    <ValidationBadge status={f.status} /> [{f.check}] {f.message}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <details>
            <summary>Query plan (debug)</summary>
            <pre className="debug-json">{JSON.stringify(result.query_plan, null, 2)}</pre>
          </details>

          {result.provenance.length > 0 && (
            <details>
              <summary>Provenance</summary>
              <pre className="debug-json">{JSON.stringify(result.provenance, null, 2)}</pre>
            </details>
          )}

          {result.debug && (
            <div className="investigation-debug">
              <h3>
                Ход исследования ({result.debug.iteration_count}{' '}
                {result.debug.iteration_count === 1 ? 'шаг' : 'шагов'})
              </h3>

              {result.debug.investigator_summary && (
                <p className="hint">Итог инвестигатора: {result.debug.investigator_summary}</p>
              )}

              {result.debug.tool_call_history.length > 0 && (
                <details open>
                  <summary>Вызовы инструментов ({result.debug.tool_call_history.length})</summary>
                  <ol className="tool-call-list">
                    {result.debug.tool_call_history.map((call, i) => (
                      <li key={i}>
                        <code>{call.tool_name}</code>
                        <pre className="debug-json">{JSON.stringify(call.input, null, 2)}</pre>
                        <pre className="debug-json">{JSON.stringify(call.output_summary, null, 2)}</pre>
                      </li>
                    ))}
                  </ol>
                </details>
              )}

              {result.debug.candidates_considered.length > 0 && (
                <details>
                  <summary>Рассмотренные варианты ({result.debug.candidates_considered.length})</summary>
                  <ul>
                    {result.debug.candidates_considered.map((c) => (
                      <li key={c.catalog_id}>
                        {c.title} — {c.catalog_id}
                        {c.unit ? ` (${c.unit})` : ''}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {result.debug.candidates_rejected.length > 0 && (
                <details>
                  <summary>Отклонённые варианты ({result.debug.candidates_rejected.length})</summary>
                  <ul>
                    {result.debug.candidates_rejected.map((c, i) => (
                      <li key={i}>
                        {c.catalog_id}: {c.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {result.debug.verification_results.length > 0 && (
                <details>
                  <summary>Раунды проверки ({result.debug.verification_results.length})</summary>
                  <ul>
                    {result.debug.verification_results.map((v, i) => (
                      <li key={i}>
                        <ValidationBadge status={v.status} />
                        {v.issues.length > 0 && (
                          <ul>
                            {v.issues.map((issue, j) => (
                              <li key={j}>
                                [{issue.category}] {issue.detail}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
