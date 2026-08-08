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
