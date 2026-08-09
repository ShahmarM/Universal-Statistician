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

export interface AskResult {
  question: string;
  query_plan: QueryPlan;
  answer: string;
  table: ComparisonTable | null;
  chart: ChartSpec | null;
  sources: Attribution[];
  provenance: Record<string, unknown>[];
  warnings: string[];
  validation: ValidationResult | null;
}
