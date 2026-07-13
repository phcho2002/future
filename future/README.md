# Al Brooks Three Push Reversal — Quantitative Framework

Complete quantitative trading system for detecting and trading the **Al Brooks Three Push Reversal** pattern. Outputs reversal probability (0-100%), expected move, expected drawdown, and risk/reward ratio.

```
╔══════════════════════════════════════════════════════════════╗
║  Input: OHLC Price Data                                     ║
║       ↓                                                     ║
║  Stage 1: Detect three-push structure (swing points)        ║
║       ↓                                                     ║
║  Stage 2: Measure exhaustion (RSI/MACD divergence, ATR)     ║
║       ↓                                                     ║
║  Stage 3: Confirm with candlestick patterns                 ║
║       ↓                                                     ║
║  Stage 4: Contextualize with EMA trend                      ║
║       ↓                                                     ║
║  Output: P(reversal) ∈ [0%, 100%] + Risk Metrics            ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Theory of Operation

### What is a Three Push Reversal?

Al Brooks identifies the **three-push pattern** as one of the highest-probability reversal setups in price action trading. The core idea:

> When price makes three consecutive moves in the same direction, each with **diminishing momentum**, the move is exhausting itself and a reversal is likely.

### Pattern Types

| Type | Structure | Implication |
|------|-----------|-------------|
| **Contracting Wedge** | Each push is shorter → converging lines | Strongest signal — classic exhaustion |
| **Parallel Channel** | Equal-sized pushes | Valid but less reliable |
| **Broadening** | Each push is longer → diverging lines | Weakest — often a trend acceleration |

### Four Confirmation Dimensions

1. **Structure** (35% weight) — The pushes themselves: count, distance decay, slope decay, overlap
2. **Exhaustion** (30% weight) — RSI divergence, MACD divergence, ATR contraction
3. **Candlestick** (20% weight) — Pin Bar, Engulfing, Outside Bar at the turn
4. **Trend** (15% weight) — EMA20/50/200 alignment and price position

---

## Files

```
./future/
├── albrooks_reversal/           # Python quantitative framework
│   ├── __init__.py              # Package exports
│   ├── features.py              # Feature calculators (swings, pushes, divergence, candles, trend)
│   ├── model.py                 # Reversal probability model (sigmoid aggregation)
│   ├── risk.py                  # Risk metrics (expected move, drawdown, RR ratio)
│   ├── engine.py                # Main engine (orchestrator)
│   └── demo.py                  # Demo with synthetic data
├── albrooks_three_push.pine     # TradingView Pine Script V5 indicator
└── README.md                    # This file
```

---

## Quick Start (Python)

### Installation

```bash
pip install numpy
```

No other dependencies required — the framework uses only NumPy.

### Basic Usage

```python
import numpy as np
from albrooks_reversal import ReversalEngine

# Assume OHLC data as numpy arrays
high  = np.array([...])
low   = np.array([...])
close = np.array([...])
open_ = np.array([...])

engine = ReversalEngine()
signal = engine.analyze(high, low, close, open_)

print(f"Reversal Probability: {signal.probability:.1f}%")
print(f"Direction: {signal.direction}")
print(f"Confidence: {signal.confidence}")
print(f"Entry: {signal.entry_price:.4f}")
print(f"Target: {signal.target_price:.4f}")
print(f"Stop Loss: {signal.stop_loss:.4f}")
print(f"R/R Ratio: {signal.risk_reward_ratio:.2f}:1")
print(f"Expected Move: {signal.expected_move_pct:.2f}%")
print(f"Expected Drawdown: {signal.expected_drawdown_pct:.2f}%")

if signal.is_actionable(min_probability=60.0, min_rr=1.5):
    print("SIGNAL: Place trade")
```

### Batch Backtesting

```python
signals = engine.analyze_batch(
    high, low, close, open_,
    window_size=200,   # 200 bars per analysis window
    step_size=1,       # analyze every bar
)

actionable = engine.get_actionable_signals(signals, min_probability=60, min_rr=1.5)
print(f"Found {len(actionable)} actionable signals in {len(signals)} windows")
```

### Running the Demo

```bash
cd future
python -m albrooks_reversal.demo
```

---

## API Reference

### `ReversalEngine`

Main analysis orchestrator.

| Method | Description |
|--------|-------------|
| `analyze(high, low, close, open_=None)` | Run full analysis → `ReversalSignal` |
| `analyze_batch(high, low, close, open_, window_size, step_size)` | Rolling window analysis → `List[ReversalSignal]` |
| `get_actionable_signals(signals, min_prob, min_rr)` | Filter to actionable signals |

### `ReversalSignal`

Complete signal output.

| Field | Type | Description |
|-------|------|-------------|
| `probability` | float | Reversal probability 0–100% |
| `direction` | str | `'bullish'` or `'bearish'` |
| `confidence` | str | `'low'`, `'medium'`, `'high'`, `'very_high'` |
| `entry_price` | float | Current close (suggested entry) |
| `target_price` | float | Take-profit target |
| `stop_loss` | float | Stop-loss level |
| `expected_move` | float | Expected price move (absolute) |
| `expected_move_pct` | float | Expected price move (%) |
| `expected_drawdown` | float | Worst-case adverse excursion |
| `expected_drawdown_pct` | float | Worst-case adverse excursion (%) |
| `risk_reward_ratio` | float | Reward / Risk ratio |
| `structure_score` | float | 0–1 structure quality |
| `exhaustion_score` | float | 0–1 exhaustion strength |
| `candle_score` | float | 0–1 candlestick confirmation |
| `trend_score` | float | 0–1 trend context |
| `pattern_detected` | bool | Whether a three-push pattern was found |
| `push_count` | int | Number of pushes detected |
| `wedge_type` | str | `'contracting'`, `'parallel'`, `'expanding'` |

### `EngineConfig`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `swing_window` | 5 | Bars each side for swing detection |
| `min_pushes` | 2 | Minimum pushes for a valid pattern |
| `momentum_decay_threshold` | 0.15 | Required momentum decay rate |
| `rsi_period` | 14 | RSI calculation period |
| `atr_period` | 14 | ATR calculation period |
| `divergence_lookback` | 20 | Bars for divergence scanning |
| `model_bias` | -2.0 | Sigmoid bias (negative = conservative) |
| `model_calibration` | 4.5 | Sigmoid steepness |
| `atr_multiplier` | 2.0 | ATR multiplier for expected move |

---

## Pine Script (TradingView)

The file `albrooks_three_push.pine` contains a complete V5 indicator for TradingView.

**Installation:**
1. Open TradingView → Pine Editor
2. Paste the entire contents of `albrooks_three_push.pine`
3. Click "Add to chart"

**Features:**
- Real-time three-push pattern detection
- RSI/MACD divergence overlay
- Candlestick pattern confirmation
- EMA20/50/200 trend context
- Background highlighting on high-probability signals
- Entry/target/stop lines drawn automatically
- Info table with live metrics
- Built-in alerts for automation

---

## Probability Model Mathematics

### Aggregation Formula

```
P(reversal) = σ(β₀ + β₁·S₁ + β₂·S₂ + β₃·S₃ + β₄·S₄) × 100

Where:
  σ(x)    = 1 / (1 + e^(-x))          [sigmoid]
  β₀      = -2.0                        [bias — conservative]
  β₁      = 4.5 × 0.35                  [structure weight]
  β₂      = 4.5 × 0.30                  [exhaustion weight]
  β₃      = 4.5 × 0.20                  [candlestick weight]
  β₄      = 4.5 × 0.15                  [trend context weight]
  S₁..S₄ ∈ [0, 1]                      [feature scores]
```

### Structure Score

```
S_structure = 0.40 × push_count_score
            + 0.25 × momentum_decay
            + 0.20 × overlap_score
            + 0.15 × symmetry_score
            + wedge_bonus
```

### Exhaustion Score

```
S_exhaustion = 0.40 × RSI_divergence
             + 0.35 × MACD_divergence
             + 0.25 × ATR_contraction
```

### Risk/Rewear Ratio

```
RR = (Expected_Move × P) / (Expected_Drawdown × (1 - P))

Where:
  Expected_Move     = 0.6 × avg_push_dist + 0.4 × (ATR × 2.0) × vol_regime
  Expected_Drawdown = ATR × (1.5 - P × 0.8) × pattern_quality
```

---

## Feature Interaction Effects

The model includes interaction effects:

| Interaction | Effect |
|-------------|--------|
| Structure × Exhaustion | Both > 0.5 → mutual boost (confirmation synergy) |
| Candlestick × Exhaustion | Both > 0.4 → candle confirmation bonus |
| Structure × Trend | Both > 0.4 → trend-aligned structure bonus |
| Structure without confirmation | Structure > 0.6 but others < 0.2 → structure penalty |

---

## Calibration

The raw sigmoid output is calibrated using Platt scaling:

```
P_calibrated = 0.85 × P_raw + 5.0 × (1 - P_raw/100)
```

This corrects for the sigmoid's tendency to cluster near 0 or 100, producing more realistic probability estimates.

---

## References

- Brooks, Al. *Reading Price Charts Bar by Bar*. Wiley, 2009.
- Brooks, Al. *Trading Price Action Trends*. Wiley, 2012.
- Brooks, Al. *Trading Price Action Reversals*. Wiley, 2012.

---

## Disclaimer

This framework is for **educational and research purposes only**. It does not constitute financial advice. Past performance does not guarantee future results. All trading involves risk. Validate thoroughly before live deployment.

---

## License

MIT — Use at your own risk.
