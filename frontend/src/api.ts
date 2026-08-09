import type {
  AskResult,
  ComparisonTable,
  IndicatorMeta,
  QueryPlan,
  SeriesResult,
  SourceDescription,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch {
    // fetch() throws (not a rejected-with-status response) on network
    // failure/CORS/refused connection — the one case with no HTTP status to
    // show, so give the most actionable message we can instead.
    throw new ApiError(`Could not reach the API at ${API_BASE}. Is the backend running?`, 0);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // body wasn't JSON — fall back to statusText, already set above
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

export function searchIndicator(query: string, limit = 20): Promise<IndicatorMeta[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return request(`/search?${params}`);
}

export function listSources(): Promise<SourceDescription[]> {
  return request('/sources');
}

export function getSeries(params: {
  sourceId: string;
  indicatorId: string;
  refArea: string;
  startPeriod?: string;
  endPeriod?: string;
}): Promise<SeriesResult> {
  const q = new URLSearchParams({
    source_id: params.sourceId,
    indicator_id: params.indicatorId,
    ref_area: params.refArea,
  });
  if (params.startPeriod) q.set('start_period', params.startPeriod);
  if (params.endPeriod) q.set('end_period', params.endPeriod);
  return request(`/series?${q}`);
}

export interface CompareRequestBody {
  source_id: string;
  indicator_id?: string;
  ref_areas?: string[];
  indicator_ids?: string[];
  ref_area?: string;
  start_period?: string;
  end_period?: string;
  growth?: boolean;
  rank?: boolean;
  ratio_to?: string;
}

export function compare(body: CompareRequestBody): Promise<ComparisonTable> {
  return request('/compare', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function ask(question: string, useLlm = false): Promise<AskResult> {
  return request('/ask', {
    method: 'POST',
    body: JSON.stringify({ question, use_llm: useLlm }),
  });
}

export function buildPlan(question: string, useLlm = false): Promise<QueryPlan> {
  return request('/plan', {
    method: 'POST',
    body: JSON.stringify({ question, use_llm: useLlm }),
  });
}
