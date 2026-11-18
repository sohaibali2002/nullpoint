/**
 * Empty defaults used before a session loads. These are deliberately blank: the UI never shows
 * fabricated strategies, metrics or portfolios, only what the API returned.
 */
import type {
  DashboardData,
  PaperRun,
  SettingsData,
  Sprint,
  Strategy,
  UsageData,
} from "./types";

export const EMPTY_DASHBOARD: DashboardData = {
  org: { id: "", name: "", monthly_budget_usd: 0 },
  sprints: [],
  strategy_counts: {},
  leaderboard: [],
  trials: 0,
  llm_spend_usd: 0,
  cost_per_candidate_usd: null,
  safety: { live_trading_enabled: false, kill_switch: false, sealed_holdout: true },
  roster: [],
  disclaimer: "Research only. Not investment advice.",
};

export const EMPTY_SETTINGS: SettingsData = {
  org: { id: "", name: "", plan: "", monthly_budget_usd: 0 },
  runtime: {},
  models: {},
  preferences: {},
  adapters: [],
  ignored_secrets:
    "Provider credentials are server-side only (Vercel environment variables). This app never asks for, accepts or returns a key.",
};

export const EMPTY_USAGE: UsageData = {
  window_days: 30,
  by_model: [],
  total_llm_spend_usd: 0,
  monthly_budget_usd: 0,
  budget_used_pct: null,
  trials: 0,
  validated_strategies: 0,
  cost_per_validated_strategy_usd: null,
};

export const EMPTY_STRATEGY: Strategy = {
  id: "",
  name: "Strategy",
  version: 1,
  status: "idea",
  tags: [],
  metrics: {},
  universe: [],
  created_at: "",
};

export const EMPTY_SPRINT: Sprint = {
  id: "",
  objective: "Project",
  universe: [],
  status: "queued",
  budget_usd: 0,
  cost_usd: 0,
  tokens_used: 0,
  trials_used: 0,
  strategies_found: 0,
  kill_requested: false,
  result: {},
  created_at: "",
  strategies: [],
};

export const EMPTY_PAPER: PaperRun = {
  run_id: "",
  strategy_id: "",
  status: "stopped",
  broker: "simulator",
  equity: 0,
  return_pct: 0,
  drift: {},
  curve: [],
};
