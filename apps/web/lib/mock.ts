/**
 * Deterministic demo dataset. Used only when no API token is present (or the API is offline),
 * so the product can be evaluated without a backend. Every surface labels it as demo data.
 */
import type {
  Agent,
  DashboardData,
  Experiment,
  Idea,
  Message,
  PaperRun,
  Portfolio,
  Provider,
  SettingsData,
  Sprint,
  Strategy,
  Universe,
  UsageData,
  Validation,
} from "./types";

function rng(seed: number) {
  let state = seed % 2147483647;
  if (state <= 0) state += 2147483646;
  return () => {
    state = (state * 16807) % 2147483647;
    return state / 2147483647;
  };
}

export function equitySeries(n = 220, seed = 7, drift = 0.00055, vol = 0.0075) {
  const rand = rng(seed);
  let value = 100;
  return Array.from({ length: n }, (_, i) => {
    const shock = (rand() - 0.5) * 2 * vol;
    value *= 1 + drift + shock;
    const date = new Date(Date.UTC(2023, 0, 2) + i * 86400000 * 1.4);
    return { date: date.toISOString().slice(0, 10), equity: Number(value.toFixed(2)) };
  });
}

const AGENTS: Agent[] = [
  { key: "orchestrator", name: "Orchestrator", purpose: "Plans research sprints, assigns work, enforces budgets and decides promote/kill.", model_slot: "orchestrator", tools: ["load_data", "list_ideas", "recall"] },
  { key: "ideator", name: "Ideator", purpose: "Generates hypotheses from factor, regime, seasonality and cross-asset logic without repeats.", model_slot: "worker", tools: ["list_ideas", "recall", "record_idea"] },
  { key: "quant", name: "Quant", purpose: "Translates a hypothesis into sandboxed signal code with explicit parameters.", model_slot: "worker", tools: ["write_signal", "backtest"] },
  { key: "engineer", name: "Engineer", purpose: "Fixes runtime failures, missing features and lookahead errors inside the sandbox.", model_slot: "worker", tools: ["write_signal", "backtest", "inspect_code"] },
  { key: "backtester", name: "Backtester", purpose: "Runs cost-aware backtests and parameter grids against point-in-time data.", model_slot: "cheap", tools: ["backtest", "run_grid"] },
  { key: "skeptic", name: "Skeptic", purpose: "Tries to kill every strategy: overfitting, leakage, fragility, regime dependence.", model_slot: "skeptic", tools: ["validate", "monte_carlo", "inspect_code"] },
  { key: "risk", name: "Risk Officer", purpose: "Checks exposure, drawdown, borrow costs and position concentration.", model_slot: "worker", tools: ["risk_report", "validate"] },
  { key: "librarian", name: "Librarian", purpose: "Deduplicates ideas by embedding and keeps lineage across strategy versions.", model_slot: "cheap", tools: ["recall", "list_ideas"] },
  { key: "portfolio", name: "Portfolio Manager", purpose: "Combines surviving strategies with HRP, risk parity or inverse-vol weights.", model_slot: "worker", tools: ["portfolio_construct"] },
  { key: "auditor", name: "Auditor", purpose: "Writes the replayable audit trail and the human-readable tearsheet for every trial.", model_slot: "cheap", tools: ["tearsheet", "recall"] },
];

const METRICS_A = {
  sharpe: 1.14,
  sortino: 1.62,
  cagr: 0.134,
  total_return: 0.61,
  max_drawdown: -0.118,
  volatility: 0.112,
  calmar: 1.13,
  win_rate: 0.548,
  profit_factor: 1.21,
  trades: 412,
  turnover: 0.21,
};

const METRICS_B = {
  sharpe: 0.87,
  sortino: 1.19,
  cagr: 0.082,
  total_return: 0.36,
  max_drawdown: -0.094,
  volatility: 0.091,
  calmar: 0.87,
  win_rate: 0.531,
  profit_factor: 1.12,
  trades: 288,
  turnover: 0.13,
};

const METRICS_C = {
  sharpe: 0.42,
  sortino: 0.55,
  cagr: 0.031,
  total_return: 0.11,
  max_drawdown: -0.163,
  volatility: 0.128,
  calmar: 0.19,
  win_rate: 0.487,
  profit_factor: 0.98,
  trades: 644,
  turnover: 0.42,
};

export const mockStrategies: Strategy[] = [
  {
    id: "stg_momentum_quality",
    name: "Sector momentum, quality-filtered",
    version: 3,
    status: "candidate",
    tags: ["cross_sectional", "momentum", "etf"],
    metrics: METRICS_A,
    trial_index: 41,
    hypothesis: "12-1 momentum in sector ETFs persists when filtered to positive 200-day trend and low realized vol.",
    universe: ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV"],
    created_at: "2026-11-09T14:22:00Z",
  },
  {
    id: "stg_vol_carry",
    name: "Volatility-scaled carry",
    version: 2,
    status: "validated",
    tags: ["vol_target", "defensive"],
    metrics: METRICS_B,
    trial_index: 37,
    hypothesis: "Scaling exposure inversely to 20-day realized volatility improves risk-adjusted returns without timing.",
    universe: ["SPY", "QQQ", "TLT", "GLD"],
    created_at: "2026-11-08T09:05:00Z",
  },
  {
    id: "stg_reversion",
    name: "Short-horizon reversal in liquid ETFs",
    version: 1,
    status: "rejected",
    tags: ["mean_reversion", "fragile"],
    metrics: METRICS_C,
    trial_index: 44,
    hypothesis: "Three-day reversals in liquid ETFs produce a stable edge after costs.",
    universe: ["SPY", "QQQ", "IWM", "XLK"],
    created_at: "2026-11-07T17:41:00Z",
  },
  {
    id: "stg_regime",
    name: "Regime-aware defensive rotation",
    version: 4,
    status: "backtested",
    tags: ["regime", "defensive"],
    metrics: { ...METRICS_B, sharpe: 0.71, cagr: 0.061 },
    trial_index: 46,
    hypothesis: "A two-state volatility regime switches between equities and duration with less drawdown.",
    universe: ["SPY", "TLT", "GLD", "XLU"],
    created_at: "2026-11-06T11:18:00Z",
  },
];

export const mockAgentMessages: Message[] = [
  { seq: 1, agent: "Orchestrator", role: "message", content: "Sprint accepted. Universe: 6 sector ETFs, 2015-2024. Budget $2.00, 3 strategy attempts, sealed holdout 2023-07-01 onward. Teams: ideation → implementation → adversarial validation.", model: "mock-orchestrator", tokens_in: 812, tokens_out: 146, cost_usd: 0.0004, created_at: "2026-11-09T14:20:02Z" },
  { seq: 2, agent: "Librarian", role: "tool", content: "recall(\"sector momentum quality\") → 3 prior ideas, 1 near-duplicate at 0.91 similarity. Flagged duplicate; ideator asked to vary the horizon.", tool_name: "recall", tool_payload: { matches: 3, max_similarity: 0.91 }, created_at: "2026-11-09T14:20:09Z" },
  { seq: 3, agent: "Ideator", role: "message", content: "Hypothesis: 12-1 cross-sectional momentum in sector ETFs survives only when filtered by positive 200-day trend and below-median 60-day volatility. Falsifiable prediction: the unfiltered version loses its edge at 3× costs.", model: "mock-worker", tokens_in: 1204, tokens_out: 212, cost_usd: 0.0006, created_at: "2026-11-09T14:20:18Z" },
  { seq: 4, agent: "Quant", role: "tool", content: "write_signal → 28 lines, no lookahead found by AST scan. Uses close[t-1] for ranking and fills at next open.", tool_name: "write_signal", tool_payload: { lines: 28, lookahead: false }, created_at: "2026-11-09T14:21:03Z" },
  { seq: 5, agent: "Backtester", role: "tool", content: "run_grid → 24 parameter combinations. Best in-sample Sharpe 1.42 (lookback 120, top_n 3). Global trial counter now 41.", tool_name: "run_grid", tool_payload: { trials: 24, best_sharpe: 1.42 }, created_at: "2026-11-09T14:22:40Z" },
  { seq: 6, agent: "Skeptic", role: "message", content: "Deflated Sharpe is 0.93 against 41 trials, but PBO is 0.44 — above the 0.35 gate. Cost stress at 3× cuts Sharpe to 0.21. This configuration is not promoted.", model: "mock-skeptic", tokens_in: 2210, tokens_out: 318, cost_usd: 0.0012, created_at: "2026-11-09T14:24:11Z" },
  { seq: 7, agent: "Engineer", role: "message", content: "Variant with weekly rebalance and a 10% position cap queued. Changing one axis only; all other parameters frozen to keep the comparison honest.", model: "mock-worker", tokens_in: 940, tokens_out: 121, cost_usd: 0.0004, created_at: "2026-11-09T14:25:02Z" },
  { seq: 8, agent: "Auditor", role: "tool", content: "tearsheet → written for the rejected configuration. Rejections are documented, not deleted. Replay hash stored.", tool_name: "tearsheet", tool_payload: { path: "artifacts/tearsheets/stg_reversion.html" }, created_at: "2026-11-09T14:25:44Z" },
  { seq: 9, agent: "Risk Officer", role: "message", content: "Approved configuration: max gross 1.0, 25% position cap, 12% vol target. Max drawdown -11.8% in the visible sample. Sealed holdout remains untouched.", model: "mock-worker", tokens_in: 1105, tokens_out: 164, cost_usd: 0.0005, created_at: "2026-11-09T14:27:10Z" },
  { seq: 10, agent: "Orchestrator", role: "message", content: "Sprint complete. 1 candidate promoted to human review, 2 rejected. LLM spend $0.011, 41 global trials. Nothing reaches paper without an explicit approval record.", model: "mock-orchestrator", tokens_in: 640, tokens_out: 102, cost_usd: 0.0003, created_at: "2026-11-09T14:27:31Z" },
];

export const mockSprints: Sprint[] = [
  {
    id: "spr_9f2c1a",
    objective: "Find cross-sectional momentum strategies that survive 3× costs on sector ETFs",
    universe: ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV"],
    status: "completed",
    budget_usd: 2,
    cost_usd: 0.0112,
    tokens_used: 18420,
    trials_used: 41,
    strategies_found: 1,
    kill_requested: false,
    result: { dataset: { synthetic: false, provider: "yahoo+stooq" }, sealed_holdout: { sealed: ["2023-07-01", "2024-12-31"] } },
    created_at: "2026-11-09T14:20:00Z",
    started_at: "2026-11-09T14:20:02Z",
    ended_at: "2026-11-09T14:27:31Z",
  },
  {
    id: "spr_7b1d4e",
    objective: "Test short-horizon reversal after costs; default to rejecting",
    universe: ["SPY", "QQQ", "IWM", "XLK"],
    status: "completed",
    budget_usd: 1.5,
    cost_usd: 0.0081,
    tokens_used: 12110,
    trials_used: 44,
    strategies_found: 0,
    kill_requested: false,
    result: { dataset: { synthetic: true, provider: "synthetic" } },
    created_at: "2026-11-07T17:38:00Z",
    started_at: "2026-11-07T17:38:04Z",
    ended_at: "2026-11-07T17:44:12Z",
  },
  {
    id: "spr_3a8f0c",
    objective: "Volatility-scaled defensive rotation with a two-state regime filter",
    universe: ["SPY", "QQQ", "TLT", "GLD"],
    status: "running",
    budget_usd: 2.5,
    cost_usd: 0.0043,
    tokens_used: 6020,
    trials_used: 18,
    strategies_found: 0,
    kill_requested: false,
    result: {},
    created_at: "2026-11-12T08:41:00Z",
    started_at: "2026-11-12T08:41:03Z",
    ended_at: null,
  },
];

export const mockValidation: Validation = {
  id: "val_01",
  strategy_id: "stg_momentum_quality",
  passed: true,
  score: 0.78,
  recommendation: "promote_to_candidate",
  payload: {
    gates: [
      { name: "dsr", passed: true, value: 0.93, threshold: ">= 0.90", detail: "Deflated Sharpe vs 41 trials" },
      { name: "pbo", passed: true, value: 0.31, threshold: "<= 0.35", detail: "Combinatorially symmetric cross-validation" },
      { name: "wfo_sharpe", passed: true, value: 0.71, threshold: ">= 0.50", detail: "Anchored walk-forward, 6 folds" },
      { name: "wfo_efficiency", passed: true, value: 0.63, threshold: ">= 0.50", detail: "OOS / IS Sharpe" },
      { name: "cost_3x", passed: true, value: 0.46, threshold: ">= 0.30", detail: "3× commission + slippage" },
      { name: "delay_1bar", passed: true, value: 0.61, threshold: ">= 0.30", detail: "One extra bar of execution delay" },
      { name: "monte_carlo", passed: true, value: 0.02, threshold: "p < 0.05", detail: "Trade bootstrap vs zero edge" },
      { name: "regime_coverage", passed: true, value: 4, threshold: ">= 3 regimes", detail: "Positive in 4 of 5 regimes" },
      { name: "minimum_track_record", passed: true, value: 412, threshold: ">= 8y and >= 100 trades", detail: "9.4 years, 412 trades" },
      { name: "sealed_holdout", passed: true, value: 1, threshold: "untouched", detail: "Holdout opened only for final review" },
    ],
    sections: {
      holdout: { opened: true, sharpe: 0.64, note: "Opened once, after the configuration was frozen." },
    },
  },
  created_at: "2026-11-09T14:26:00Z",
};

export const mockDashboard: DashboardData = {
  org: { id: "org_demo", name: "Skeptical Capital", monthly_budget_usd: 40 },
  sprints: mockSprints,
  strategy_counts: { candidate: 1, validated: 2, rejected: 9, backtested: 3, paper: 1 },
  leaderboard: mockStrategies.filter((s) => s.status !== "rejected").slice(0, 3),
  trials: 41,
  llm_spend_usd: 0.0112,
  cost_per_candidate_usd: 0.0112,
  safety: { live_trading_enabled: false, kill_switch: false, sealed_holdout: true },
  roster: AGENTS,
  disclaimer: "Research only. Not investment advice. Backtests are hypotheses, not forecasts.",
};

export const mockProviders: Provider[] = [
  { name: "yahoo", requires_key: false, asset_classes: ["equity", "etf", "fx", "index", "crypto"], license_note: "Yahoo Finance public endpoints are for PERSONAL, non-commercial use. Do not redistribute data. Replace with a licensed feed before any commercial launch.", homepage: "https://finance.yahoo.com", rate_limit_per_min: 30, point_in_time: false, available: true },
  { name: "stooq", requires_key: false, asset_classes: ["equity", "etf", "index", "fx"], license_note: "Stooq free downloads are for personal use with rate limits; no redistribution. Daily data may be split-adjusted but is not dividend-adjusted.", homepage: "https://stooq.com", rate_limit_per_min: 20, point_in_time: false, available: true },
  { name: "fred", requires_key: false, asset_classes: ["macro"], license_note: "FRED series are subject to per-series copyright (e.g. S&P data is not redistributable). Free API key recommended; respect FRED terms of use.", homepage: "https://fred.stlouisfed.org", rate_limit_per_min: 60, point_in_time: true, available: true },
  { name: "sec_edgar", requires_key: false, asset_classes: ["fundamentals"], license_note: "SEC EDGAR is public; set a descriptive User-Agent and respect the 10 requests/second fair-access limit.", homepage: "https://www.sec.gov/edgar", rate_limit_per_min: 600, point_in_time: true, available: true },
  { name: "fama_french", requires_key: false, asset_classes: ["factors"], license_note: "Fama-French factor files are free for academic and personal research; cite the source.", homepage: "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/", rate_limit_per_min: 30, point_in_time: true, available: true },
  { name: "alpha_vantage", requires_key: true, asset_classes: ["equity", "etf", "fx", "crypto"], license_note: "Free tier is limited (25 requests/day). Check commercial redistribution terms.", homepage: "https://www.alphavantage.co", rate_limit_per_min: 5, point_in_time: false, available: false },
  { name: "tiingo", requires_key: true, asset_classes: ["equity", "etf", "crypto"], license_note: "Free tier for personal use; commercial use requires a paid plan.", homepage: "https://www.tiingo.com", rate_limit_per_min: 50, point_in_time: false, available: false },
  { name: "polygon", requires_key: true, asset_classes: ["equity", "etf", "fx", "crypto"], license_note: "Free tier: 5 calls/minute, end-of-day only. Redistribution requires a licence.", homepage: "https://polygon.io", rate_limit_per_min: 5, point_in_time: false, available: false },
  { name: "alpaca", requires_key: true, asset_classes: ["equity", "etf", "crypto"], license_note: "Market data under Alpaca terms; paper account only in Nullpoint. No live order routing.", homepage: "https://alpaca.markets", rate_limit_per_min: 200, point_in_time: false, available: false },
  { name: "binance", requires_key: false, asset_classes: ["crypto"], license_note: "Public market-data endpoints; respect exchange rate limits and local regulation.", homepage: "https://www.binance.com", rate_limit_per_min: 60, point_in_time: false, available: true },
];

export const mockUniverses: Universe[] = [
  { universe: "us_sector_etfs", known: true, label: "US sector ETFs (SPDR)", symbols: ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE"], asset_class: "equity_etf", survivorship_bias: true, warning: "SURVIVORSHIP BIAS: this universe contains current constituents only. Backtests on it overstate historical performance.", note: "Current SPDR sector lineup. Funds were launched at different dates; pre-launch history is absent." },
  { universe: "mega_cap_tech", known: true, label: "Mega-cap technology", symbols: ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"], asset_class: "equity", survivorship_bias: true, warning: "SURVIVORSHIP BIAS: today's winners were not knowable in the past.", note: "Hand-picked current leaders; use for demos only, never as evidence." },
  { universe: "crypto_major", known: true, label: "Crypto majors", symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"], asset_class: "crypto", survivorship_bias: false, warning: "24/7 markets, exchange counterparty risk and regime breaks. Funding and borrow costs matter.", note: "Public Binance/ccxt endpoints. Not available in all jurisdictions." },
  { universe: "macro_us", known: true, label: "US macro series", symbols: ["DGS10", "DGS2", "CPI", "UNRATE"], asset_class: "macro", survivorship_bias: false, warning: "FRED series are revised. Without vintage data, macro backtests can see revised history.", note: "Point-in-time flags vary by series." },
];

export const mockIdeas: Idea[] = [
  { id: "idea_1", title: "Trend-filtered sector momentum", hypothesis: "12-1 momentum persists only when the sector is above its 200-day average and volatility is below median.", family: "cross_sectional", tags: ["momentum", "trend"], status: "tested", origin: "agent", created_at: "2026-11-09T14:20:18Z" },
  { id: "idea_2", title: "Volatility-scaled carry", hypothesis: "Inverse-vol exposure scaling improves Sharpe without requiring return forecasts.", family: "vol_target", tags: ["defensive"], status: "tested", origin: "agent", created_at: "2026-11-08T09:03:00Z" },
  { id: "idea_3", title: "Post-earnings drift in large caps", hypothesis: "Positive surprise plus muted price reaction predicts 20-day drift after costs.", family: "event", tags: ["earnings", "drift"], status: "new", origin: "human", created_at: "2026-11-06T12:00:00Z" },
  { id: "idea_4", title: "Credit spread regime switch", hypothesis: "High-yield spread widening predicts equity drawdowns with a 5-day lag.", family: "regime", tags: ["macro", "credit"], status: "duplicate", origin: "agent", dedupe_of: "idea_9", dedupe_score: 0.93, created_at: "2026-11-05T16:30:00Z" },
  { id: "idea_5", title: "Turn-of-month liquidity", hypothesis: "Month-end rebalancing flows create a small, cost-sensitive equity drift.", family: "seasonality", tags: ["calendar"], status: "rejected", origin: "agent", created_at: "2026-11-04T10:12:00Z" },
  { id: "idea_6", title: "Duration hedge with vol targeting", hypothesis: "A vol-targeted long-duration sleeve reduces portfolio drawdown without killing CAGR.", family: "defensive", tags: ["rates", "hedge"], status: "new", origin: "human", created_at: "2026-11-03T09:00:00Z" },
];

export const mockExperiments: Experiment[] = [
  { id: "exp_41", strategy_id: "stg_momentum_quality", kind: "grid", engine: "native", params: { lookback: 120, top_n: 3 }, metrics: METRICS_A, data_hash: "sha256:81ac…", code_hash: "sha256:4d02…", seed: 7, trial_index: 41, is_oos: false, split: "in_sample", created_at: "2026-11-09T14:22:40Z" },
  { id: "exp_40", strategy_id: "stg_momentum_quality", kind: "walk_forward", engine: "native", params: { folds: 6, anchored: true }, metrics: { sharpe: 0.71, efficiency: 0.63 }, trial_index: 41, is_oos: true, split: "walk_forward", created_at: "2026-11-09T14:23:50Z" },
  { id: "exp_39", strategy_id: "stg_vol_carry", kind: "validation", engine: "native", params: { costs: "3x" }, metrics: { sharpe: 0.46 }, trial_index: 37, is_oos: true, split: "stress", created_at: "2026-11-08T10:10:00Z" },
  { id: "exp_38", strategy_id: "stg_reversion", kind: "grid", engine: "native", params: { lookback: 3, top_n: 2 }, metrics: METRICS_C, trial_index: 44, is_oos: false, created_at: "2026-11-07T17:42:00Z" },
  { id: "exp_37", strategy_id: "stg_reversion", kind: "monte_carlo", engine: "native", params: { paths: 1000 }, metrics: { p_value: 0.21 }, trial_index: 44, is_oos: true, created_at: "2026-11-07T17:43:30Z" },
  { id: "exp_36", strategy_id: "stg_regime", kind: "backtest", engine: "native", params: { states: 2 }, metrics: { sharpe: 0.71 }, trial_index: 46, is_oos: false, created_at: "2026-11-06T11:20:00Z" },
];

export const mockPaper: PaperRun[] = [
  { run_id: "paper_1", strategy_id: "stg_momentum_quality", status: "running", broker: "simulator", equity: 104210.55, return_pct: 0.0421, drift: { sharpe_backtest: 1.14, sharpe_paper: 0.92, note: "Simulated bootstrap, not observed market performance.", synthetic: true }, disclaimer: "Paper trading only. Simulated unless a paper broker is connected." },
  { run_id: "paper_0", strategy_id: "stg_vol_carry", status: "stopped", broker: "simulator", equity: 100980.1, return_pct: 0.0098, drift: { sharpe_backtest: 0.87, sharpe_paper: 0.74, note: "Stopped by operator.", synthetic: true }, disclaimer: "Paper trading only." },
];

export const mockPortfolios: Portfolio[] = [
  { id: "pf_1", name: "HRP · 3 strategies", method: "hrp", weights: { "Sector momentum, quality-filtered": 0.31, "Volatility-scaled carry": 0.44, "Regime-aware defensive rotation": 0.25 }, metrics: { sharpe: 1.28, cagr: 0.118, max_drawdown: -0.076, volatility: 0.082, diversification: 1.42 }, created_at: "2026-11-10T09:00:00Z" },
];

export const mockSettings: SettingsData = {
  org: { id: "org_demo", name: "Skeptical Capital", plan: "research", monthly_budget_usd: 40 },
  runtime: { sprint_budget_usd: 2, sprint_wall_clock_minutes: 25, max_agent_iterations: 4, llm_provider: "mock", job_runner: "auto", allow_network: true, sandbox_backend: "docker", kill_switch: false, live_trading_enabled: false },
  models: { orchestrator: "claude-sonnet-4-20250514", worker: "claude-sonnet-4-20250514", skeptic: "claude-opus-4-20250514", cheap: "claude-haiku-4-5-20251001" },
  preferences: { nightly_enabled: false, alert_slack: true, alert_email: false },
  keys: { anthropic: "sk-ant-…9f2a", fred: null, alpha_vantage: null, tiingo: null, polygon: null, alpaca: null, slack: "https://hooks.slack.com/…", discord: null, sentry: null },
  adapters: ["native", "vectorbt (not installed)", "backtesting.py (not installed)"],
  ignored_secrets: "Keys are never returned in full. Set them in the server environment, never in the UI.",
};

export const mockUsage: UsageData = {
  window_days: 30,
  by_model: [
    { model: "claude-sonnet-4-20250514", cost_usd: 6.42, tokens: 1_840_000 },
    { model: "claude-opus-4-20250514", cost_usd: 3.18, tokens: 420_000 },
    { model: "claude-haiku-4-5-20251001", cost_usd: 0.61, tokens: 2_100_000 },
  ],
  total_llm_spend_usd: 10.21,
  monthly_budget_usd: 40,
  budget_used_pct: 25.5,
  trials: 186,
  validated_strategies: 4,
  cost_per_validated_strategy_usd: 2.55,
  note: "Free-tier data providers cost $0; LLM spend is the only metered cost in v0.",
};

export const mockAgents: Agent[] = AGENTS;
