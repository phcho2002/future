import argparse
from dataclasses import asdict
from pprint import pprint

from future_quant.engine import QuantEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run future-quant analysis with xtquant data.")
    parser.add_argument("--symbol", default="IF0", help="DB futures symbol, e.g. IF0, RB0, M0")
    parser.add_argument(
        "--exchange",
        default="cffex",
        help="Exchange code (cffex/shfe/dce/czce/gfex/ine); required to qualify the symbol",
    )
    parser.add_argument("--period", default="15", help="Minute period, e.g. 5 or 15")
    parser.add_argument("--account-equity", type=float, default=None)
    args = parser.parse_args()

    engine = QuantEngine()
    result = engine.analyze_futures_minute(
        symbol=args.symbol,
        exchange=args.exchange,
        period=args.period,
        account_equity=args.account_equity,
    )

    pprint(
        {
            "market_state": asdict(result.market_state),
            "channel": asdict(result.channel),
            "push_count": len(result.push_set.pushes),
            "exhaustion_score": result.push_set.exhaustion_score,
            "exhaustion_details": result.push_set.exhaustion_details,
            "signal": asdict(result.signal),
        }
    )


if __name__ == "__main__":
    main()
