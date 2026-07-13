"""全市场扫描器：遍历沪深主板，运行二次突破状态机，输出近期信号。

用法:
    from scanner import Scanner
    scanner = Scanner("config.yaml")
    df = scanner.scan_and_save()
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from engine import SecondBreakoutEngine, StrategyParams
import indicators as ind
from tdx_reader import TDXLocalProvider, limit_pct_of

# 全局变量（供 ProcessPoolExecutor 子进程使用，通过 initializer 注入）
_CFG: dict = {}
_PROVIDER: TDXLocalProvider | None = None
_PARAMS: StrategyParams | None = None


def _init_worker(config_path: str):
    """子进程初始化：加载配置，构建 Provider 和参数。"""
    global _CFG, _PROVIDER, _PARAMS
    with open(config_path, encoding="utf-8") as f:
        _CFG = yaml.safe_load(f)
    _PROVIDER = TDXLocalProvider(_CFG["data"]["tdx_vipdoc"])
    _PARAMS = StrategyParams.from_dict(_CFG["strategy"])


def _scan_one(symbol: str) -> dict | None:
    """扫描单只股票，返回近期信号（dict）或 None。子进程内执行。"""
    try:
        df = _PROVIDER.history(symbol, lookback=_CFG["data"]["lookback_bars"])
        if len(df) < _PARAMS.pattern_lookback + _PARAMS.ema_period:
            return None

        engine = SecondBreakoutEngine(df, _PARAMS)
        signals = engine.run()
        if not signals:
            return None

        # 筛选最近 signal_days 天的信号
        signal_days = _CFG["scan"]["signal_days"]
        latest_date = df["date"].iloc[-1]
        cutoff = latest_date - pd.Timedelta(days=signal_days * 1.5)  # 日历日宽松
        recent = [s for s in signals if pd.Timestamp(s.date) >= cutoff]
        if not recent:
            return None

        s = recent[-1]  # 取最近一个信号
        row = df.iloc[s.idx]

        # 量比过滤
        min_vol_ratio = _CFG["scan"]["min_volume_ratio"]
        vol_ma20 = df["volume"].iloc[max(0, s.idx - 20): s.idx].mean()
        vol_ratio = float(row["volume"] / vol_ma20) if vol_ma20 > 0 else 0.0
        if vol_ratio < min_vol_ratio:
            return None

        # 涨跌停检测（按板块区分 10% / 20%）
        prev_close = float(df["close"].iloc[s.idx - 1]) if s.idx > 0 else s.close
        limit_pct = limit_pct_of(symbol, _CFG["backtest"]["limit_pct"])
        near_limit = ind.is_limit_up(s.close, prev_close, limit_pct)

        pct_above_res = (s.close - s.resistance) / s.resistance * 100
        pct_above_ema = (s.close - s.ema200) / s.ema200 * 100

        return {
            "symbol": symbol,
            "signal_date": str(s.date)[:10],
            "close": round(s.close, 2),
            "resistance": round(s.resistance, 2),
            "atr": round(s.atr, 2),
            "atr_pct": round(s.atr / s.close * 100, 2),
            "pct_above_resistance": round(pct_above_res, 2),
            "box_range_pct": round(s.box_range_pct * 100, 2),
            "pct_above_ema200": round(pct_above_ema, 2),
            "vol_ratio": round(vol_ratio, 2),
            "near_limit": near_limit,
        }
    except Exception:
        return None


class Scanner:
    """全市场扫描器。"""

    def __init__(self, config_path: str | Path = "config.yaml"):
        with open(config_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.provider = TDXLocalProvider(self.cfg["data"]["tdx_vipdoc"])
        self.params = StrategyParams.from_dict(self.cfg["strategy"])
        self.config_path = str(config_path)
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)

    def _scan_serial(self, symbols: list[str], verbose: bool = True) -> list[dict]:
        """串行扫描（调试用）。"""
        results = []
        for k, sym in enumerate(symbols):
            if verbose and (k + 1) % 200 == 0:
                print(f"  进度 {k + 1}/{len(symbols)}...")
            r = self._scan_one_inline(sym)
            if r:
                results.append(r)
        return results

    def _scan_one_inline(self, symbol: str) -> dict | None:
        """主进程内扫描单只股票（供串行模式使用）。"""
        try:
            df = self.provider.history(symbol, lookback=self.cfg["data"]["lookback_bars"])
            if len(df) < self.params.pattern_lookback + self.params.ema_period:
                return None
            engine = SecondBreakoutEngine(df, self.params)
            signals = engine.run()
            if not signals:
                return None
            signal_days = self.cfg["scan"]["signal_days"]
            latest_date = df["date"].iloc[-1]
            cutoff = latest_date - pd.Timedelta(days=signal_days * 1.5)
            recent = [s for s in signals if pd.Timestamp(s.date) >= cutoff]
            if not recent:
                return None
            s = recent[-1]
            row = df.iloc[s.idx]
            min_vol_ratio = self.cfg["scan"]["min_volume_ratio"]
            vol_ma20 = df["volume"].iloc[max(0, s.idx - 20): s.idx].mean()
            vol_ratio = float(row["volume"] / vol_ma20) if vol_ma20 > 0 else 0.0
            if vol_ratio < min_vol_ratio:
                return None
            prev_close = float(df["close"].iloc[s.idx - 1]) if s.idx > 0 else s.close
            limit_pct = limit_pct_of(symbol, self.cfg["backtest"]["limit_pct"])
            near_limit = ind.is_limit_up(s.close, prev_close, limit_pct)
            pct_above_res = (s.close - s.resistance) / s.resistance * 100
            pct_above_ema = (s.close - s.ema200) / s.ema200 * 100
            return {
                "symbol": symbol,
                "signal_date": str(s.date)[:10],
                "close": round(s.close, 2),
                "resistance": round(s.resistance, 2),
                "atr": round(s.atr, 2),
                "atr_pct": round(s.atr / s.close * 100, 2),
                "pct_above_resistance": round(pct_above_res, 2),
                "box_range_pct": round(s.box_range_pct * 100, 2),
                "pct_above_ema200": round(pct_above_ema, 2),
                "vol_ratio": round(vol_ratio, 2),
                "near_limit": near_limit,
            }
        except Exception:
            return None

    def scan(
        self,
        symbols: list[str] | None = None,
        workers: int | None = None,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """扫描全市场（或指定股票），返回信号 DataFrame。

        Args:
            symbols: 股票代码列表，None=自动扫描全部沪深主板
            workers: 并行进程数，None=从 config 读取，1=串行
            verbose: 打印进度
        """
        if symbols is None:
            symbols = self.provider.all_main_board_symbols()
        if verbose:
            print(f"扫描 {len(symbols)} 只股票...")

        if workers is None:
            workers = self.cfg["scan"].get("workers", 8)

        if workers <= 1:
            results = self._scan_serial(symbols, verbose)
        else:
            results = []
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_worker,
                initargs=(self.config_path,),
            ) as pool:
                futures = {pool.submit(_scan_one, sym): sym for sym in symbols}
                done = 0
                for fut in as_completed(futures):
                    done += 1
                    if verbose and done % 500 == 0:
                        print(f"  进度 {done}/{len(symbols)}...")
                    r = fut.result()
                    if r:
                        results.append(r)

        if verbose:
            print(f"扫描完成，命中 {len(results)} 个信号")

        df = pd.DataFrame(results)
        if not df.empty:
            df = df.sort_values("signal_date", ascending=False).reset_index(drop=True)
        return df

    def scan_and_save(self, symbols: list[str] | None = None, workers: int | None = None) -> pd.DataFrame:
        """扫描并保存到 output/scan_signals_YYYYMMDD.csv。"""
        df = self.scan(symbols=symbols, workers=workers)
        today = datetime.now().strftime("%Y%m%d")
        out = self.output_dir / f"scan_signals_{today}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n信号已保存: {out}")
        if not df.empty:
            print(f"\n最新信号（共 {len(df)} 条）:")
            cols = [
                "symbol", "signal_date", "close", "resistance", "atr_pct",
                "pct_above_resistance", "box_range_pct", "pct_above_ema200",
                "vol_ratio", "near_limit",
            ]
            print(df[cols].to_string(index=False))
        return df
