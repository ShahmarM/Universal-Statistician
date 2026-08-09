export interface Attribution {
  source_id: string;
  source_name: string;
  dataset_id: string;
  retrieved_at: string;
  source_url: string | null;
}

export interface SourceDescription {
  source_id: string;
  source_name: string;
  dataflow_id: string;
  website: string;
}

export interface IndicatorMeta {
  indicator_id: string;
  name: string;
  source_id: string;
  description: string | null;
}

export interface Observation {
  period: string;
  value: number | null;
}

export interface SeriesResult {
  indicator_id: string;
  ref_area: string;
  frequency: string;
  observations: Observation[];
  attribution: Attribution;
}

export interface ComparisonColumn {
  key: string;
  label: string;
  derived: boolean;
  attribution: Attribution | null;
}

export interface ComparisonRow {
  period: string;
  [columnKey: string]: string | number | null;
}

export interface ComparisonTable {
  columns: ComparisonColumn[];
  rows: ComparisonRow[];
}

// ---- /ask (Phase 11) --------------------------------------------------------

export interface CandidateIndicator {
  indicator_id: string;
  source_id: string;
  name: string;
  concept: string;
  unit: string | null;
  frequency: string | null;
  geographic_coverage: string[] | null;
}

export interface TransformationSpec {
  operation: string;
  numerator_concept: string | null;
  denominator_concept: string | null;
  input_concept: string | null;
  base_period: string | null;
  base_value: number | null;
  left_concept: string | null;
  right_concept: string | null;
  inputs: string[];
  weights: number[];
  output_name: string | null;
}

export interface QueryPlan {
  question: string;
  concepts: string[];
  candidate_indicators: CandidateIndicator[];
  geographies: string[];
  start_period: string | null;
  end_period: string | null;
  frequency: string | null;
  transformations: TransformationSpec[];
  comparison: string | null;
  ranking: boolean;
  output_type: string;
  assumptions: string[];
  needs_clarification: boolean;
  clarification_question: string | null;
  selected_indicators: CandidateIndicator[];
  validation_notes: string[];
}

export interface ValidationFinding {
  status: 'PASS' | 'WARNING' | 'FAIL';
  check: string;
  message: string;
}

export interface ValidationResult {
  status: 'PASS' | 'WARNING' | 'FAIL';
  findings: ValidationFinding[];
}

export interface ChartSeriesSpec {
  key: string;
  label: string;
}

export interface ChartSpec {
  chart_type: 'line' | 'bar' | 'comparison';
  title: string;
  subtitle: string | null;
  x_axis: string;
  y_axis: string;
  units: string | null;
  series: ChartSeriesSpec[];
  source_note: string;
}

// ---- agent modes (Phase 8): Fast/Research/Auto -----------------------------
//
// Fast mode's `query_plan` is the QueryPlan shape above (from core/ask.py's
// legacy pipeline). Research mode's is agent/state.py's
// InvestigationState.evidence_package() instead — a different shape sharing
// only a few fields (assumptions/warnings). `AskResult.query_plan` is typed
// as the union of both since either can come back depending on `mode_used`;
// only fields present in both are safe to read without checking `mode_used`
// first.

// One table cell's stable identity (agent/evidence.py, task section 2) --
// what an LLM-written answer must cite to ground a number. Keyed by
// evidence_id ("{result_id}@{period}") in EvidencePackage.evidence.
export interface EvidenceEntry {
  evidence_id: string;
  result_id: string;
  period: string;
  value: number;
  unit: string | null;
  value_kind: string;
  indicator_id: string | null;
  geography: string | null;
  source_id: string | null;
  source_name: string | null;
  dataset_id: string | null;
  operation: string | null;
  formula: string | null;
  input_evidence_ids: string[];
}

export interface EvidencePackage {
  question: string;
  table: ComparisonTable;
  evidence: Record<string, EvidenceEntry>;
  candidates_considered: Record<string, unknown>[];
  candidates_rejected: Record<string, unknown>[];
  result_ids: Record<string, unknown>;
  assumptions: string[];
  unresolved_ambiguities: string[];
  warnings: string[];
  validation_results: Record<string, unknown>[];
  provenance_references: Record<string, unknown>[];
}

export interface ToolCallRecord {
  iteration: number;
  tool_name: string;
  input: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  duration_ms: number;
}

export interface CandidateSummary {
  catalog_id: string;
  source_id: string;
  indicator_id: string;
  title: string;
  organization: string | null;
  dataset_id: string | null;
  unit: string | null;
  frequency: string | null;
  price_basis: string | null;
  seasonally_adjusted: boolean | null;
  geographic_coverage: string[] | null;
  official_url: string | null;
  search_rank: number;
  search_score_note: string;
}

export interface RejectedCandidate {
  catalog_id: string;
  reason: string;
}

export interface VerificationIssue {
  category: string;
  detail: string;
}

export interface VerificationReport {
  status: 'PASS' | 'WARNING' | 'FAIL';
  issues: VerificationIssue[];
}

// The investigation's full audit trail, only present when the request set
// debug=true — never contains hidden chain-of-thought, only the tool calls
// actually made and the investigator's own short final summary (see
// agent/loop.py: it produces tool calls plus that summary, nothing else).
export interface DebugTrail {
  iteration_count: number;
  tool_call_history: ToolCallRecord[];
  candidates_considered: CandidateSummary[];
  candidates_rejected: RejectedCandidate[];
  verification_results: VerificationReport[];
  investigator_summary: string;
}

export type AskMode = 'auto' | 'fast' | 'research';

export interface AskResult {
  question: string;
  query_plan: QueryPlan | EvidencePackage;
  answer: string;
  table: ComparisonTable | null;
  chart: ChartSpec | null;
  sources: Attribution[];
  provenance: Record<string, unknown>[];
  warnings: string[];
  validation: ValidationResult | null;
  mode_used: 'fast' | 'research';
  verification: VerificationReport | null;
  debug?: DebugTrail;
}
