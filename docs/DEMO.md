# Five-minute demo

Goal: show agents doing research, a rejection with receipts, and a survivor reaching human review —
without paid keys. Everything below runs offline on synthetic data unless you omit `--synthetic`.

## 1. Backend

```bash
cp .env.example .env
uv sync --extra dev
export LLM_PROVIDER=mock JOB_RUNNER=sync ALLOW_NETWORK=false
uv run uvicorn api.main:app --port 8000
```

## 2. One sprint, from the CLI

```bash
python scripts/demo_sprint.py --synthetic
```

You get JSON: sprint id, trials, spend, dataset (`synthetic: true`), sealed holdout window, strategies
with statuses and tearsheet paths. A typical run produces at least one rejection; a promotion is not
guaranteed, and that is the point.

## 3. The same thing in the UI

```bash
npm --prefix apps/web install
npm --prefix apps/web run dev
```

Open http://localhost:3000 → **Continue with demo data** to explore every screen without an account.
To use the real backend, register at `/login`, then:

1. **Agent sprints** → objective + universe → *Run agents*. In sync mode the page returns a completed
   run; with the worker it streams.
2. Open the sprint → read the audit trail: hypotheses, code, trials, skeptic verdicts. Note that the
   rejected strategy has a tearsheet too.
3. **Strategies** → open a candidate → **Validation** tab → every gate, pass or fail. Try approving to
   `live_ready` from the API and observe the 400: there is no executor.
4. **Approve for paper** → **Paper trading** → start a run → *+10d*. The drift panel repeats that this is
   simulated.
5. **Data explorer** → provider licences and survivorship warnings.
6. **Settings** → budget, model slots, key presence (never values).

## 4. Replay and receipts

```bash
# every message, ordered
curl -s localhost:8000/sprints/<id>/messages -H "authorization: Bearer $TOKEN" | jq '.count'

# every gate on the strategy
curl -s localhost:8000/strategies/<id>/validation -H "authorization: Bearer $TOKEN" | jq '.payload.gates'
```

## 5. What to point out

- The trial counter is global and monotonic; the deflated Sharpe uses it.
- The holdout is sealed before agents start; only a human opens it, once, on a frozen spec.
- A failed gate blocks promotion regardless of Sharpe.
- Rejections are preserved with tearsheets.
- No live order path exists; `live_ready` is a label.
- Synthetic data is labelled everywhere it travels.

## Speaker notes

> "Most quant demos show a beautiful equity curve. This one shows the audit trail that decides whether
> the curve means anything. The agents are allowed to fail; the platform is built to make failure
> visible."
