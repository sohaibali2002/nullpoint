# Nullpoint brand system

**Tagline:** Nothing ships until it beats the null.

**Positioning (one sentence):** Nullpoint is a multi-agent research platform where AI teams
write, backtest and falsify systematic strategies, and only strategies that survive
anti-overfitting validation reach the leaderboard.

**What we are not:** a signal group, a robo-advisor, a "10x strategy" shop, or financial advice.

## Voice

Precise, calm, skeptical. We state what was measured, on what data, with what uncertainty.
We never promise returns. We are comfortable saying "this did not survive."

| Do | Don't |
|---|---|
| "Sharpe 1.1 after 3× costs, p(overfit) 0.31 — not promoted." | "Crushing it with a 1.1 Sharpe!" |
| "10 agents, 1 sealed holdout, 0 live-trading hooks." | "Fully autonomous trading AI." |
| "Backtests are hypotheses, not forecasts." | "Proven edge." |
| Short sentences. Numbers with units. | Exclamation marks, emoji, rocket ships. |

**Signature phrases:** *beat the null* · *sealed holdout* · *falsification gauntlet* ·
*every trial counts* · *research only*.

## Palette (dark-first)

| Token | Hex | Use |
|---|---|---|
| `ink` | `#07080A` | Page background |
| `graphite` | `#101218` | Cards, panels |
| `slate` | `#171A22` | Raised surfaces, table stripes |
| `line` | `#252A36` | Borders, grid lines |
| `text` | `#E9ECF3` | Primary text |
| `muted` | `#99A1B3` | Secondary text, labels |
| `signal` | `#5FE3C0` | Primary accent: verified, positive, CTA |
| `beam` | `#7AA2FF` | Secondary accent: agents, links, info |
| `warn` | `#F2B34B` | Warnings, survivorship-bias flags |
| `danger` | `#FF6B6B` | Rejections, drawdown, kill switch |
| `paper` | `#F5F3EE` | Light-mode surfaces, tearsheet print |

Contrast: `text` on `ink` ≈ 16:1; `signal` on `ink` ≈ 11:1; `muted` on `graphite` ≈ 5.6:1.

## Type

- **UI / display:** Inter (400/500/600), tight tracking on headings, `font-feature-settings: "tnum"` in tables.
- **Data / code:** JetBrains Mono (400/500). All numbers, tickers, metrics and code render in mono.
- Rule: never a metric without a unit, a period, and a verification state.

## Logo

- **Mark:** a hollow ring (the null / the origin) with a solid point offset outside it —
  the point that must be proven, not the center that is assumed.
- Files: `logo.svg` (mark), `wordmark.svg` (mark + text), `favicon.svg`, `og.svg` (1200×630).
- Clear space: one ring diameter on all sides. Minimum size 20px mark, 96px wordmark.
- On light surfaces use `#07080A` mark; never place the mark on a photo, gradient, or mid-tone accent.
- Don't rotate, outline, add shadows, or recolor the point in anything but `signal`.

## Surfaces

Dark by default; light mode ("paper") only for printed tearsheets. Charts use a 6-color sequence
derived from the palette: `signal`, `beam`, `warn`, `danger`, `muted`, `text`. Never red/green only —
rejections are labeled, not just colored.
