"""Strategy template library used by the Ideator and Strategy Coder.

Each template is a complete, point-in-time-safe signal with declared features and a search grid.
They are deliberately simple and economically motivated: the platform's value is falsification,
not clever signals. Every template must pass the AST scanner and the future-data canary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StrategyTemplate:
    key: str
    name: str
    family: str
    hypothesis: str
    rationale: str
    features: dict[str, dict[str, Any]]
    params: dict[str, Any]
    grid: dict[str, list[Any]]
    signal_code: str
    risk: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "family": self.family,
            "hypothesis": self.hypothesis,
            "rationale": self.rationale,
            "features": self.features,
            "params": self.params,
            "grid": self.grid,
            "risk": self.risk,
            "tags": list(self.tags),
        }


XS_MOMENTUM = '''
def signal(features, params, close):
    lookback = int(params.get("lookback", 20))
    top_n = int(params.get("top_n", 3))
    mom = features[f"momentum_{lookback}"]
    vol = features["volatility"].clip(lower=1e-4)
    score = mom / vol
    ranks = score.rank(axis=1, ascending=False, method="first")
    longs = (ranks <= top_n).astype(float)
    shorts = (ranks > ranks.shape[1] - top_n).astype(float) if params.get("long_short", True) else 0.0
    raw = longs - shorts
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

MEAN_REVERSION = '''
def signal(features, params, close):
    lookback = int(params.get("lookback", 20))
    entry = float(params.get("entry", 1.0))
    z = features["zscore"]
    regime = (features["trend"] > float(params.get("trend_filter", -1.0))).astype(float)
    raw = -np.sign(z) * (z.abs() > entry).astype(float) * regime
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

TREND_FOLLOWING = '''
def signal(features, params, close):
    lookback = int(params.get("lookback", 100))
    trend = features[f"sma_{lookback}"] / close - 1.0
    vol = features["volatility"].clip(lower=1e-4)
    strength = (trend / vol).clip(-2.0, 2.0)
    raw = np.sign(strength) * strength.abs().clip(lower=float(params.get("min_strength", 0.25)))
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

LOW_VOL_DEFENSIVE = '''
def signal(features, params, close):
    top_n = int(params.get("top_n", 3))
    vol = features["volatility"]
    ranks = vol.rank(axis=1, ascending=True, method="first")
    raw = (ranks <= top_n).astype(float)
    trend = features["trend"]
    raw = raw * (trend > float(params.get("trend_floor", -0.02))).astype(float)
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

VOL_REGIME_RISK_ON = '''
def signal(features, params, close):
    vol = features["volatility"]
    window = int(params.get("regime_window", 120))
    threshold = vol.rolling(window, min_periods=window // 2).median()
    risk_on = (vol < threshold).astype(float)
    mom = features["momentum_20"]
    ranks = mom.rank(axis=1, ascending=False, method="first")
    basket = (ranks <= int(params.get("top_n", 3))).astype(float)
    raw = basket * risk_on * float(params.get("gross", 1.0))
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

TURN_OF_MONTH = '''
def signal(features, params, close):
    days = np.asarray(close.index.day)
    window = int(params.get("window", 3))
    in_window = ((days >= 28 - window) | (days <= window)).astype(float)
    mask = pd.DataFrame(
        np.repeat(in_window[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
    raw = mask * (features["trend"] > 0).astype(float)
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

BETA_NEUTRAL_MOMENTUM = '''
def signal(features, params, close):
    lookback = int(params.get("lookback", 20))
    mom = features[f"momentum_{lookback}"]
    beta = features["beta"].clip(lower=0.2, upper=3.0)
    score = mom / beta
    ranks = score.rank(axis=1, ascending=False, method="first")
    top_n = int(params.get("top_n", 3))
    longs = (ranks <= top_n).astype(float) / beta
    shorts = (ranks > ranks.shape[1] - top_n).astype(float) / beta
    raw = longs - shorts
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''

BETA_FEATURES = {"beta": {"type": "beta", "window": 60}}

TEMPLATES: dict[str, StrategyTemplate] = {
    "xs_momentum": StrategyTemplate(
        key="xs_momentum",
        name="Cross-sectional momentum with a volatility floor",
        family="cross_sectional",
        hypothesis="Assets with stronger risk-adjusted trailing returns continue to outperform over 1-3 months.",
        rationale="Momentum is one of the most replicated cross-sectional anomalies; volatility scaling reduces crash risk.",
        features={
            "momentum_10": {"type": "momentum", "window": 10},
            "momentum_20": {"type": "momentum", "window": 20},
            "momentum_40": {"type": "momentum", "window": 40},
            "volatility": {"type": "volatility", "window": 20},
        },
        params={"lookback": 20, "top_n": 3, "long_short": True},
        grid={"lookback": [10, 20, 40], "top_n": [2, 3]},
        signal_code=XS_MOMENTUM,
        risk={"max_position_weight": 0.35, "max_gross_exposure": 1.0},
        tags=("momentum", "cross-sectional", "equity"),
    ),
    "xs_mean_reversion": StrategyTemplate(
        key="xs_mean_reversion",
        name="Short-horizon cross-sectional mean reversion",
        family="mean_reversion",
        hypothesis="Short-term extreme moves partially reverse when the broader trend is intact.",
        rationale="Liquidity provision and overreaction effects produce reversal at daily-to-weekly horizons.",
        features={
            "zscore": {"type": "zscore", "window": 20},
            "volatility": {"type": "volatility", "window": 20},
            "trend": {"type": "trend", "fast": 20, "slow": 100},
        },
        params={"lookback": 20, "entry": 1.0, "trend_filter": -1.0},
        grid={"entry": [0.75, 1.0, 1.5]},
        signal_code=MEAN_REVERSION,
        risk={"max_position_weight": 0.35, "max_gross_exposure": 0.75},
        tags=("mean-reversion", "cross-sectional", "equity"),
    ),
    "trend_following": StrategyTemplate(
        key="trend_following",
        name="Time-series trend following",
        family="trend",
        hypothesis="Assets above their long moving average continue to trend; below it they keep falling.",
        rationale="Time-series momentum is documented across futures, FX and equity indices.",
        features={
            "sma_50": {"type": "sma", "window": 50},
            "sma_100": {"type": "sma", "window": 100},
            "sma_200": {"type": "sma", "window": 200},
            "volatility": {"type": "volatility", "window": 20},
        },
        params={"lookback": 100, "min_strength": 0.25},
        grid={"lookback": [50, 100, 200]},
        signal_code=TREND_FOLLOWING,
        risk={"max_position_weight": 0.35, "max_gross_exposure": 1.0, "vol_target_annual": 0.12},
        tags=("trend", "time-series", "multi-asset"),
    ),
    "low_vol_defensive": StrategyTemplate(
        key="low_vol_defensive",
        name="Low-volatility defensive tilt",
        family="defensive",
        hypothesis="Lower-volatility assets earn higher risk-adjusted returns and fall less in drawdowns.",
        rationale="The low-volatility anomaly is persistent but sensitive to rate regimes and crowding.",
        features={
            "volatility": {"type": "volatility", "window": 20},
            "trend": {"type": "trend", "fast": 20, "slow": 100},
        },
        params={"top_n": 3, "trend_floor": -0.02},
        grid={"top_n": [2, 3, 4]},
        signal_code=LOW_VOL_DEFENSIVE,
        risk={"max_position_weight": 0.4, "max_gross_exposure": 1.0, "long_short": False},
        tags=("low-vol", "defensive", "long-only"),
    ),
    "vol_regime_risk_on": StrategyTemplate(
        key="vol_regime_risk_on",
        name="Volatility-regime risk-on momentum",
        family="regime",
        hypothesis="Momentum pays when realized volatility is below its trailing median and bleeds otherwise.",
        rationale="Momentum crashes cluster in high-volatility rebound regimes; a regime filter is a defensible conditioner.",
        features={
            "volatility": {"type": "volatility", "window": 20},
            "momentum_20": {"type": "momentum", "window": 20},
        },
        params={"top_n": 3, "regime_window": 120, "gross": 1.0},
        grid={"top_n": [2, 3], "regime_window": [60, 120]},
        signal_code=VOL_REGIME_RISK_ON,
        risk={"max_position_weight": 0.35, "max_gross_exposure": 1.0, "long_short": False},
        tags=("regime", "momentum", "volatility"),
    ),
    "turn_of_month": StrategyTemplate(
        key="turn_of_month",
        name="Turn-of-month seasonality conditioned on trend",
        family="seasonality",
        hypothesis="Flows around month-end create a small positive drift, but only when the trend is up.",
        rationale="Calendar effects are weak, decay quickly and are a good test of whether the validator rejects thin edges.",
        features={"trend": {"type": "trend", "fast": 20, "slow": 100}},
        params={"window": 3},
        grid={"window": [2, 3, 5]},
        signal_code=TURN_OF_MONTH,
        risk={"max_position_weight": 0.35, "max_gross_exposure": 1.0, "long_short": False},
        tags=("seasonality", "calendar", "equity"),
    ),
    "beta_neutral_momentum": StrategyTemplate(
        key="beta_neutral_momentum",
        name="Beta-neutral momentum",
        family="cross_sectional",
        hypothesis="Scaling momentum by market beta isolates idiosyncratic persistence from market exposure.",
        rationale="Beta-neutralisation can turn a levered market bet into a genuine cross-sectional signal.",
        features={
            **BETA_FEATURES,
            "momentum_20": {"type": "momentum", "window": 20},
        },
        params={"lookback": 20, "top_n": 3},
        grid={"lookback": [20], "top_n": [2, 3]},
        signal_code=BETA_NEUTRAL_MOMENTUM,
        risk={"max_position_weight": 0.35, "max_gross_exposure": 1.0},
        tags=("momentum", "beta-neutral", "cross-sectional"),
    ),
}


def get_template(key: str) -> StrategyTemplate:
    if key not in TEMPLATES:
        raise KeyError(f"unknown template {key!r}")
    return TEMPLATES[key]


def families() -> list[str]:
    return sorted({template.family for template in TEMPLATES.values()})
