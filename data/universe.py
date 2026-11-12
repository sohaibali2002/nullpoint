"""Universe definitions and the survivorship-bias warning that must accompany them.

These lists are *current* constituents, which is exactly the survivorship bias the platform must
disclose. The warning is surfaced in the API payload and rendered in the Data Explorer.
"""

from __future__ import annotations

from typing import Any

UNIVERSES: dict[str, dict[str, Any]] = {
    "us_sector_etfs": {
        "label": "US sector ETFs (SPDR)",
        "symbols": ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE"],
        "asset_class": "equity_etf",
        "survivorship_bias": True,
        "note": "Current SPDR sector lineup. Funds were launched at different dates; pre-launch history is absent.",
    },
    "us_broad_etfs": {
        "label": "US broad-market ETFs",
        "symbols": ["SPY", "QQQ", "IWM", "DIA", "MDY", "VTI", "TLT", "GLD", "HYG", "UUP"],
        "asset_class": "equity_etf",
        "survivorship_bias": False,
        "note": "Liquid, long-history ETFs; still subject to ETF closure bias in the tail.",
    },
    "mega_cap": {
        "label": "US mega-cap survivors",
        "symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "XOM", "UNH"],
        "asset_class": "equity",
        "survivorship_bias": True,
        "note": "Today's largest names. Backtests on this list are optimistically biased by construction.",
    },
    "crypto_majors": {
        "label": "Crypto majors",
        "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
        "asset_class": "crypto",
        "survivorship_bias": False,
        "note": "24/7 markets; the engine annualizes with 252 periods, so crypto metrics are conservative.",
    },
    "fx_majors": {
        "label": "FX majors (futures proxies)",
        "symbols": ["EURUSD=X", "JPY=X", "GBPUSD=X", "AUDUSD=X"],
        "asset_class": "fx",
        "survivorship_bias": False,
        "note": "Yahoo FX quotes are indicative, not executable. Costs are understated without broker data.",
    },
    "factor_proxies": {
        "label": "Fama-French factor proxies",
        "symbols": ["MTUM", "VLUE", "SIZE", "QUAL", "USMV"],
        "asset_class": "equity_etf",
        "survivorship_bias": True,
        "note": "ETF proxies for factors, not the academic factors themselves (see Fama-French provider).",
    },
}


def universe_notice(name: str) -> dict[str, Any]:
    universe = UNIVERSES.get(name)
    if universe is None:
        return {"universe": name, "known": False, "warning": "Custom universe: no coverage metadata."}
    warning = None
    if universe["survivorship_bias"]:
        warning = (
            "SURVIVORSHIP BIAS: this universe contains current constituents only. "
            "Backtests on it overstate historical performance."
        )
    return {
        "universe": name,
        "known": True,
        "label": universe["label"],
        "symbols": universe["symbols"],
        "asset_class": universe["asset_class"],
        "survivorship_bias": universe["survivorship_bias"],
        "warning": warning,
        "note": universe["note"],
    }
