"""
Demo Script — Al Brooks Three Push Reversal System
===================================================
Generates synthetic price data with three-push patterns and runs
the complete analysis pipeline, printing detailed results.

Usage:
    python -m albrooks_reversal.demo
    # or
    python demo.py
"""

import numpy as np

# Allow running directly or as module
try:
    from .engine import ReversalEngine, EngineConfig
    from .model import ReversalProbabilityModel
except ImportError:
    from engine import ReversalEngine, EngineConfig
    from model import ReversalProbabilityModel


def generate_three_push_bullish(n_bars: int = 300) -> tuple:
    """
    Generate synthetic price data containing a three-push-down
    bullish reversal pattern.

    Structure:
    1. Initial uptrend (for context)
    2. Three pushes down (each weaker than the last)
    3. Reversal up
    """
    np.random.seed(42)

    # Base price
    base = 100.0
    noise_level = 0.15

    # Generate timeline
    t = np.arange(n_bars)
    close = np.zeros(n_bars)
    high = np.zeros(n_bars)
    low = np.zeros(n_bars)
    open_ = np.zeros(n_bars)

    # 1. Initial downtrend (bars 0-60) — to establish lower lows context
    for i in range(60):
        close[i] = base - i * 0.15 + np.random.randn() * noise_level

    # 2. Three pushes down (bars 60-200) — each push makes a lower low with weakening momentum
    seg = np.linspace(0, 1, 140)

    # Push 1 down: strong decline (bars 60-100)
    push1 = close[59] - seg[:40] * 10.0 + np.random.randn(40) * noise_level * 0.6
    # Retrace 1: bounce up (bars 100-115)
    ret1 = push1[-1] + seg[40:55] * 4.0 + np.random.randn(15) * noise_level * 0.4
    # Push 2 down: weaker decline (bars 115-135)
    push2 = ret1[-1] - seg[55:75] * 5.5 + np.random.randn(20) * noise_level * 0.5
    # Retrace 2: smaller bounce (bars 135-148)
    ret2 = push2[-1] + seg[75:88] * 2.5 + np.random.randn(13) * noise_level * 0.4
    # Push 3 down: weakest decline — exhaustion (bars 148-165)
    push3 = ret2[-1] - seg[88:105] * 3.0 + np.random.randn(17) * noise_level * 0.5

    close[60:100] = push1
    close[100:115] = ret1
    close[115:135] = push2
    close[135:148] = ret2
    close[148:165] = push3

    # 3. Reversal up — strong rally (bars 165-300)
    recovery = close[164] + seg[105:140] * 12.0 + np.random.randn(35) * noise_level * 0.7
    close[165:200] = recovery
    for i in range(200, n_bars):
        close[i] = close[i - 1] + 0.10 + np.random.randn() * noise_level * 0.5

    # Generate OHLC from close
    for i in range(1, n_bars):
        bar_range = abs(close[i] - close[i - 1]) + noise_level * 2
        open_[i] = close[i - 1]
        high[i] = max(open_[i], close[i]) + np.random.random() * bar_range * 0.5
        low[i] = min(open_[i], close[i]) - np.random.random() * bar_range * 0.5

    open_[0] = close[0]
    high[0] = close[0] + noise_level
    low[0] = close[0] - noise_level

    return high, low, close, open_


def generate_three_push_bearish(n_bars: int = 300) -> tuple:
    """
    Generate synthetic price data with a three-push-up bearish reversal.
    """
    np.random.seed(123)

    base = 100.0
    noise_level = 0.15

    close = np.zeros(n_bars)
    high = np.zeros(n_bars)
    low = np.zeros(n_bars)
    open_ = np.zeros(n_bars)

    # Initial uptrend (bars 0-60) — to establish higher highs context
    for i in range(60):
        close[i] = base + i * 0.15 + np.random.randn() * noise_level

    # Three pushes up (bars 60-200) — each push makes a higher high with weakening momentum
    seg = np.linspace(0, 1, 140)

    # Push 1 up: strong rally (bars 60-100)
    close[60:100] = close[59] + seg[:40] * 10.0 + np.random.randn(40) * noise_level * 0.6
    # Retrace 1: pullback (bars 100-115)
    close[100:115] = close[99] - seg[40:55] * 4.0 + np.random.randn(15) * noise_level * 0.4
    # Push 2 up: weaker rally (bars 115-135)
    close[115:135] = close[114] + seg[55:75] * 5.5 + np.random.randn(20) * noise_level * 0.5
    # Retrace 2: smaller pullback (bars 135-148)
    close[135:148] = close[134] - seg[75:88] * 2.5 + np.random.randn(13) * noise_level * 0.4
    # Push 3 up: weakest rally — exhaustion (bars 148-165)
    close[148:165] = close[147] + seg[88:105] * 3.0 + np.random.randn(17) * noise_level * 0.5

    # Reversal down — strong decline (bars 165-300)
    close[165:200] = close[164] - seg[105:140] * 12.0 + np.random.randn(35) * noise_level * 0.7
    for i in range(200, n_bars):
        close[i] = close[i - 1] - 0.10 + np.random.randn() * noise_level * 0.5

    # Generate OHLC
    for i in range(1, n_bars):
        bar_range = abs(close[i] - close[i - 1]) + noise_level * 2
        open_[i] = close[i - 1]
        high[i] = max(open_[i], close[i]) + np.random.random() * bar_range * 0.5
        low[i] = min(open_[i], close[i]) - np.random.random() * bar_range * 0.5

    open_[0] = close[0]
    high[0] = close[0] + noise_level
    low[0] = close[0] - noise_level

    return high, low, close, open_


def print_separator(title: str):
    """Print a formatted separator."""
    width = 70
    print()
    print('═' * width)
    print(f'  {title}')
    print('═' * width)


def main():
    """Run the demo."""
    print_separator('Al Brooks Three Push Reversal — Demo')
    print()

    # Configuration
    config = EngineConfig(
        swing_window=5,
        min_pushes=2,
        momentum_decay_threshold=0.15,
    )

    engine = ReversalEngine(config)

    # ── Bullish Scenario ──
    print_separator('Scenario 1: Three Pushes DOWN → Bullish Reversal')

    high, low, close, open_ = generate_three_push_bullish(300)
    result = engine.analyze(high, low, close, open_)

    print()
    print(f'  Pattern Detected: {result.pattern_detected}')
    print(f'  Direction:        {result.direction}')
    print(f'  Push Count:       {result.push_count}')
    print(f'  Wedge Type:       {result.wedge_type}')
    print(f'  Momentum Decay:   {result.momentum_decay:.3f}')
    print()
    print(f'  ── Probability Assessment ──')
    print(f'  Reversal Probability: {result.probability:.1f}%')
    print(f'  Confidence:           {result.confidence}')
    print(f'  Structure Score:      {result.structure_score:.3f}')
    print(f'  Exhaustion Score:     {result.exhaustion_score:.3f}')
    print(f'  Candle Score:         {result.candle_score:.3f}')
    print(f'  Trend Score:          {result.trend_score:.3f}')
    print()
    print(f'  ── Risk Metrics ──')
    print(f'  Entry Price:      {result.entry_price:.4f}')
    print(f'  Target Price:     {result.target_price:.4f}')
    print(f'  Stop Loss:        {result.stop_loss:.4f}')
    print(f'  Expected Move:    {result.expected_move_pct:.2f}%')
    print(f'  Expected Drawdown:{result.expected_drawdown_pct:.2f}%')
    print(f'  R/R Ratio:        {result.risk_reward_ratio:.2f}:1')
    print(f'  Actionable:       {result.is_actionable()}')

    # ── Bearish Scenario ──
    print_separator('Scenario 2: Three Pushes UP → Bearish Reversal')

    high, low, close, open_ = generate_three_push_bearish(300)
    result = engine.analyze(high, low, close, open_)

    print()
    print(f'  Pattern Detected: {result.pattern_detected}')
    print(f'  Direction:        {result.direction}')
    print(f'  Push Count:       {result.push_count}')
    print(f'  Wedge Type:       {result.wedge_type}')
    print(f'  Momentum Decay:   {result.momentum_decay:.3f}')
    print()
    print(f'  ── Probability Assessment ──')
    print(f'  Reversal Probability: {result.probability:.1f}%')
    print(f'  Confidence:           {result.confidence}')
    print(f'  Structure Score:      {result.structure_score:.3f}')
    print(f'  Exhaustion Score:     {result.exhaustion_score:.3f}')
    print(f'  Candle Score:         {result.candle_score:.3f}')
    print(f'  Trend Score:          {result.trend_score:.3f}')
    print()
    print(f'  ── Risk Metrics ──')
    print(f'  Entry Price:      {result.entry_price:.4f}')
    print(f'  Target Price:     {result.target_price:.4f}')
    print(f'  Stop Loss:        {result.stop_loss:.4f}')
    print(f'  Expected Move:    {result.expected_move_pct:.2f}%')
    print(f'  Expected Drawdown:{result.expected_drawdown_pct:.2f}%')
    print(f'  R/R Ratio:        {result.risk_reward_ratio:.2f}:1')
    print(f'  Actionable:       {result.is_actionable()}')

    # ── Batch Analysis ──
    print_separator('Scenario 3: Rolling Batch Analysis (backtest simulation)')

    high, low, close, open_ = generate_three_push_bullish(400)
    signals = engine.analyze_batch(
        high, low, close, open_,
        window_size=200,
        step_size=20,
    )

    actionable = engine.get_actionable_signals(signals)
    print(f'  Total windows analyzed: {len(signals)}')
    print(f'  Actionable signals:     {len(actionable)}')

    if actionable:
        print()
        print(f'  ── Actionable Signals ──')
        for i, sig in enumerate(actionable[:5]):
            print(f'  [{i+1}] Bar {sig.bar_index}: {sig.direction.upper()} '
                  f'| Prob: {sig.probability:.1f}% '
                  f'| RR: {sig.risk_reward_ratio:.2f}:1 '
                  f'| Conf: {sig.confidence}')

    # ── Explanation ──
    print_separator('Explanation of Latest Signal')

    if signals:
        # Re-run model explanation on the last signal
        model = ReversalProbabilityModel(bias=-2.0, calibration_factor=4.5)
        last = signals[-1]

        # Create a minimal result for explanation
        try:
            from .model import ReversalProbabilityResult
        except ImportError:
            from model import ReversalProbabilityResult
        dummy = ReversalProbabilityResult(
            probability=last.probability,
            direction=last.direction,
            confidence=last.confidence,
            structure_score=last.structure_score,
            exhaustion_score=last.exhaustion_score,
            candle_score=last.candle_score,
            trend_score=last.trend_score,
            pattern_info={
                'direction': last.direction,
                'push_count': last.push_count,
                'wedge_type': last.wedge_type,
                'momentum_decay': last.momentum_decay,
                'symmetry_score': 0.5,
                'completion_bar': last.bar_index,
                'pushes': [],
            } if last.pattern_detected else None,
            exhaustion_info=last.exhaustion_detail,
            candle_info=last.candle_detail,
            trend_info=last.trend_detail,
        )

        explanation = model.explain(dummy)
        print()
        print(explanation)

    print_separator('Demo Complete')
    print()
    print('  Usage in production:')
    print('    from albrooks_reversal import ReversalEngine')
    print('    engine = ReversalEngine()')
    print('    signal = engine.analyze(high, low, close, open_)')
    print('    if signal.is_actionable():')
    print('        print(f"Trade: {signal.direction} at {signal.entry_price}")')
    print('        print(f"Target: {signal.target_price}, Stop: {signal.stop_loss}")')
    print()


if __name__ == '__main__':
    main()
