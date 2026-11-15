"""Render the README validation-gate table from ``engine.validation.GATES``.

Run ``python scripts/render_gates.py`` to update the marked block, or
``python scripts/render_gates.py --check`` in CI to fail when the docs drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine.validation import CRITICAL_GATES, GATES

START = "<!-- gates:start -->"
END = "<!-- gates:end -->"

ROWS = [
    ("walk_forward_oos_sharpe", "Walk-forward OOS Sharpe", "oos_sharpe_min", ">="),
    ("wfo_efficiency", "WFO efficiency (OOS / IS)", "wfo_efficiency_min", ">="),
    ("cpcv_median_sharpe", "CPCV median OOS Sharpe (train-only params)", "cpcv_median_sharpe_min", ">="),
    ("pbo", "PBO / CSCV", "pbo_max", "<="),
    ("deflated_sharpe", "Deflated Sharpe (per-period units, effective trials)", "dsr_min", ">="),
    ("monte_carlo_block_bootstrap", "Block-bootstrap P(SR <= 0)", "mc_prob_le_zero_max", "<="),
    ("random_entry_null", "Random-entry null p-value", "random_entry_p_max", "<="),
    ("signal_shuffle_null", "Placebo shuffle p-value (same costs/turnover)", "signal_shuffle_p_max", "<="),
    ("cost_3x", "Sharpe at 3x costs, +1 bar", "cost_3x_sharpe_min", ">="),
    ("execution_delay", "Sharpe with +1 extra bar delay", "delay_sharpe_min", ">="),
    ("parameter_plateau", "Parameter plateau share", "plateau_share_min", ">="),
    ("minimum_track_record", "Minimum track record", "min_trades", ">="),
    ("regime_robustness", "Worst regime Sharpe (60+ days, >= 4 regimes)", "regime_min_sharpe", ">="),
]


def render() -> str:
    lines = [
        START,
        "| Gate | Threshold | Critical | Source key |",
        "|---|---|:---:|---|",
    ]
    for gate, label, key, operator in ROWS:
        value = GATES[key]
        if gate == "minimum_track_record":
            threshold = f">= {GATES['min_years']:g} years and >= {value:g} trades"
        else:
            threshold = f"{operator} {value:g}"
        critical = "yes" if gate in CRITICAL_GATES else ""
        lines.append(f"| {label} | {threshold} | {critical} | `{key}` |")
    lines.append("| Sealed holdout | one-time, human-only, logged before opening | yes | policy |")
    lines.append("")
    lines.append(
        "Generated from `engine/validation.py` by `scripts/render_gates.py`. "
        "Do not edit this table by hand."
    )
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--readme", default="README.md")
    args = parser.parse_args()
    path = Path(args.readme)
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"README is missing {START} / {END} markers")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    rendered = render()
    updated = before + rendered + after
    if args.check:
        if updated != text:
            print("README gate table is stale; run python scripts/render_gates.py")
            return 1
        print("README gate table matches GATES")
        return 0
    path.write_text(updated, encoding="utf-8")
    print("updated README gate table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
