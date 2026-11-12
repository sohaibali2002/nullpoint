# Data sources, licences and bias

Nullpoint is built for free-tier research data. Free does not mean unrestricted. Each provider exposes
a licence note through `/data/providers`, and the UI shows it next to availability. This document is a
summary, not legal advice; verify terms before commercial use.

| Provider | Key | Licence / constraints | Point-in-time |
|---|---|---|---|
| Yahoo Finance | no | Public endpoints are for **personal, non-commercial** use. Do not redistribute. Unofficial and rate-limited. | no |
| Stooq | no | Personal use, rate-limited, no redistribution. Daily data is split-adjusted but not dividend-adjusted. | no |
| FRED | optional | Per-series copyright; some series (e.g. S&P) are not redistributable. Respect FRED terms. | yes (some) |
| SEC EDGAR | no | Public filings. Set a descriptive User-Agent and stay under 10 req/s. | yes |
| Fama-French | no | Free for academic/personal research; cite the source. | yes |
| Binance / ccxt | no | Public market data; respect exchange limits and local regulation. 24/7 regime breaks. | no |
| Alpha Vantage | yes | Free tier ~25 requests/day. Check commercial redistribution terms. | no |
| Tiingo | yes | Free tier for personal use; commercial requires a paid plan. | no |
| Polygon | yes | Free tier end-of-day, 5 req/min. Redistribution requires a licence. | no |
| Alpaca | yes | Market data under Alpaca terms. Nullpoint uses **paper only**, read-only. | no |

## Bias warnings recorded with results

- **Survivorship bias.** Sector, mega-cap and most hand-built universes contain *current* constituents.
  Backtests on them overstate historical performance. The API returns this warning with the universe and
  the UI displays it; it is not a solved problem.
- **Revised macro data.** FRED series are revised. Without vintage data a macro backtest can see the
  future. Providers flagged `point_in_time: false` should be treated as indicative.
- **Adjusted prices.** Split/dividend adjustments are not a substitute for total-return index data.
- **Synthetic fallback.** When providers fail or network is disabled, the pipeline generates synthetic
  prices. This is labelled `synthetic: true` in the dataset, experiment ledger, API responses, tearsheet
  and UI. Synthetic results verify the pipeline only and are never market evidence.
- **Delisted assets.** Not modelled. Strategies that rely on shorting or illiquid names are optimistic.
- **Costs.** Commission/spread/slippage/borrow are explicit and stressed at 2× and 3×, but real impact
  and capacity are not simulated.

## Redistribution

Do not commit downloaded market data to this repository. The lake is a local cache. If you publish
results, publish the code, hashes and methodology, not the underlying vendor data, unless the licence
allows it.
