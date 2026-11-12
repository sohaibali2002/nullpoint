export type Status = "idea" | "backtested" | "validated" | "candidate" | "paper" | "live_ready" | "rejected" | "retired";

export interface Strategy {
  id: string;
  name: string;
  version: number;
  status: Status;
  tags: string[];
  metrics: Record<string, number>;
  trial_index?: number;
  hypothesis?: string;
  universe?: string[];
  parent_id?: string | null;
  sprint_id?: string | null;
  code?: string;
  code_hash?: string;
  spec?: Record<string, unknown>;
  created_at: string;
  disclaimer?: string;
}

export interface Sprint {
  id: string;
  objective: string;
  universe: string[];
  status: string;
  budget_usd: number;
  cost_usd: number;
  tokens_used: number;
  trials_used: number;
  strategies_found: number;
  kill_requested: boolean;
  result: Record<string, any>;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  strategies?: Strategy[];
}

export interface Message {
  seq: number;
  agent: string;
  role: string;
  content: string;
  tool_name?: string | null;
  tool_payload?: Record<string, any>;
  model?: string | null;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  created_at: string;
}

export interface Gate {
  name: string;
  passed: boolean;
  value: number | null;
  threshold: string;
  detail: string;
}

export interface Validation {
  id: string;
  strategy_id: string;
  passed: boolean;
  score: number;
  recommendation: string;
  payload: {
    gates?: Gate[];
    sections?: Record<string, any>;
    [key: string]: any;
  };
  created_at: string;
}

export interface Agent {
  key: string;
  name: string;
  purpose: string;
  model_slot: string;
  tools: string[];
}

export interface DashboardData {
  org: { id: string; name: string; monthly_budget_usd: number };
  sprints: Sprint[];
  strategy_counts: Record<string, number>;
  leaderboard: Strategy[];
  trials: number;
  llm_spend_usd: number;
  cost_per_candidate_usd: number | null;
  safety: { live_trading_enabled: boolean; kill_switch: boolean; sealed_holdout: boolean };
  roster: Agent[];
  disclaimer: string;
}

export interface Provider {
  name: string;
  requires_key: boolean;
  asset_classes: string[];
  license_note: string;
  homepage: string;
  rate_limit_per_min: number;
  point_in_time: boolean;
  available?: boolean;
}

export interface Universe {
  universe: string;
  known: boolean;
  label: string;
  symbols: string[];
  asset_class: string;
  survivorship_bias: boolean;
  warning: string;
  note: string;
}

export interface Idea {
  id: string;
  title: string;
  hypothesis: string;
  family: string;
  tags: string[];
  status: string;
  origin: string;
  dedupe_of?: string | null;
  dedupe_score?: number;
  created_at: string;
}

export interface Experiment {
  id: string;
  strategy_id: string;
  sprint_id?: string | null;
  kind: string;
  engine: string;
  params: Record<string, unknown>;
  metrics: Record<string, number>;
  data_hash?: string;
  code_hash?: string;
  seed?: number;
  trial_index?: number;
  is_oos?: boolean;
  split?: string;
  env?: Record<string, unknown>;
  created_at: string;
  dates?: string[];
  returns?: number[];
  equity?: number[];
}

export interface PaperRun {
  run_id: string;
  strategy_id: string;
  status: string;
  broker: string;
  equity: number;
  return_pct: number;
  drift: Record<string, any>;
  disclaimer?: string;
  curve?: { date: string; equity: number; ret: number }[];
}

export interface Portfolio {
  id: string;
  name: string;
  method: string;
  weights: Record<string, number>;
  metrics: Record<string, number>;
  created_at: string;
}

export interface SettingsData {
  org: { id: string; name: string; plan: string; monthly_budget_usd: number };
  runtime: Record<string, any>;
  models: Record<string, string>;
  preferences: Record<string, any>;
  keys: Record<string, string | null>;
  adapters?: string[];
  ignored_secrets?: string;
}

export interface UsageData {
  window_days: number;
  by_model: { model: string; cost_usd: number; tokens: number }[];
  total_llm_spend_usd: number;
  monthly_budget_usd: number;
  budget_used_pct: number | null;
  trials: number;
  validated_strategies: number;
  cost_per_validated_strategy_usd: number | null;
  note?: string;
}
