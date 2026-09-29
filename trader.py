"""
IBKR stock trader using ib_insync.

Prerequisites:
  1. TWS or IB Gateway running locally
  2. Enable API: TWS > Edit > Global Configuration > API > Settings
     - Check "Enable ActiveX and Socket Clients"
     - Socket port: 7497 (paper) or 7496 (live)
     - Uncheck "Read-Only API" to allow order placement
  3. pip install ib_insync pandas

Usage:
  python trader.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pandas as pd
from ib_insync import IB, BarDataList, LimitOrder, MarketOrder, Option, Stock, Trade, util


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TWS_HOST = "127.0.0.1"
TWS_PORT = 4001          # 4001 = Gateway live  / 7496 = TWS live
                         # 4002 = Gateway paper / 7497 = TWS paper
CLIENT_ID = 1

# Market data type: 1=live (needs API subscription), 3=delayed free 15-min lag
MKT_DATA_TYPE = 3


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def connect() -> IB:
    ib = IB()
    # timeout=None skips blocking init requests that can time out on some Gateway versions
    ib.connect(TWS_HOST, TWS_PORT, clientId=CLIENT_ID, timeout=None)
    # 3 = delayed (free, 15-min lag) — upgrade to 1 if you have an API market data subscription
    ib.reqMarketDataType(3)
    print(f"Connected  accounts={ib.managedAccounts()}")
    return ib


def disconnect(ib: IB) -> None:
    ib.disconnect()
    print("Disconnected")


# ---------------------------------------------------------------------------
# Account / portfolio
# ---------------------------------------------------------------------------

def print_account(ib: IB) -> None:
    tags = ["NetLiquidation", "TotalCashValue", "BuyingPower", "UnrealizedPnL"]
    values = {av.tag: f"{av.value} {av.currency}"
              for av in ib.accountValues()
              if av.tag in tags}
    print("\n--- Account ---")
    for k, v in values.items():
        print(f"  {k}: {v}")


def print_positions(ib: IB) -> None:
    positions = ib.positions()
    if not positions:
        print("\nNo open positions")
        return
    rows = [
        {
            "symbol": p.contract.symbol,
            "qty": p.position,
            "avg_cost": round(p.avgCost, 4),
        }
        for p in positions
    ]
    print("\n--- Positions ---")
    print(pd.DataFrame(rows).to_string(index=False))


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float


def get_quote(ib: IB, symbol: str, exchange: str = "SMART") -> Quote:
    contract = Stock(symbol, exchange, "USD")
    ib.qualifyContracts(contract)
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(2)
    ib.cancelMktData(contract)
    return Quote(
        symbol=symbol,
        bid=ticker.bid,
        ask=ticker.ask,
        last=ticker.last,
        volume=ticker.volume,
    )


def get_historical_bars(
    ib: IB,
    symbol: str,
    duration: str = "5 D",
    bar_size: str = "1 hour",
) -> pd.DataFrame:
    """Return a DataFrame of OHLCV bars."""
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    bars: BarDataList = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=True,
    )
    df = util.df(bars)[["date", "open", "high", "low", "close", "volume"]]
    return df


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def buy_market(ib: IB, symbol: str, qty: int) -> Trade:
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    order = MarketOrder("BUY", qty)
    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    print(f"BUY MARKET {qty} {symbol}  status={trade.orderStatus.status}")
    return trade


def sell_market(ib: IB, symbol: str, qty: int) -> Trade:
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    order = MarketOrder("SELL", qty)
    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    print(f"SELL MARKET {qty} {symbol}  status={trade.orderStatus.status}")
    return trade


def buy_limit(ib: IB, symbol: str, qty: int, limit_price: float) -> Trade:
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    order = LimitOrder("BUY", qty, limit_price)
    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    print(f"BUY LIMIT {qty} {symbol} @ {limit_price}  status={trade.orderStatus.status}")
    return trade


def sell_limit(ib: IB, symbol: str, qty: int, limit_price: float) -> Trade:
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    order = LimitOrder("SELL", qty, limit_price)
    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    print(f"SELL LIMIT {qty} {symbol} @ {limit_price}  status={trade.orderStatus.status}")
    return trade


def buy_bracket(
    ib: IB,
    symbol: str,
    qty: int,
    entry_price: float,
    take_profit: float,
    stop_loss: float,
) -> list[Trade]:
    """Bracket order: limit entry + automatic take-profit + stop-loss."""
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    orders = ib.bracketOrder(
        action="BUY",
        quantity=qty,
        limitPrice=entry_price,
        takeProfitPrice=take_profit,
        stopLossPrice=stop_loss,
    )
    trades = [ib.placeOrder(contract, o) for o in orders]
    ib.sleep(1)
    print(
        f"BRACKET BUY {qty} {symbol}: entry={entry_price} "
        f"TP={take_profit} SL={stop_loss}"
    )
    return trades


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

def get_option_chain(ib: IB, symbol: str) -> None:
    """Print available expirations and a sample of strikes."""
    stock = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(stock)
    chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
    if not chains:
        print("No option chain found")
        return
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
    expirations = sorted(chain.expirations)[:8]
    strikes = sorted(chain.strikes)
    print(f"\n{symbol} option chain  (exchange={chain.exchange}  multiplier={chain.multiplier})")
    print(f"  Next expirations : {' | '.join(expirations)}")
    print(f"  Strike range     : {strikes[0]} – {strikes[-1]}  ({len(strikes)} strikes total)")


def buy_option(
    ib: IB,
    symbol: str,
    expiry: str,       # YYYYMMDD
    strike: float,
    right: str,        # C or P
    qty: int,
    limit_price: float | None = None,
) -> Trade:
    contract = Option(symbol, expiry, strike, right.upper(), "SMART", currency="USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError(f"Could not qualify option {symbol} {expiry} {strike} {right}")
    order = LimitOrder("BUY", qty, limit_price) if limit_price else MarketOrder("BUY", qty)
    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    label = f"{right.upper()} {strike} exp={expiry}"
    print(f"BUY OPTION {qty}x {symbol} {label}  status={trade.orderStatus.status}")
    return trade


def sell_option(
    ib: IB,
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    qty: int,
    limit_price: float | None = None,
) -> Trade:
    contract = Option(symbol, expiry, strike, right.upper(), "SMART", currency="USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError(f"Could not qualify option {symbol} {expiry} {strike} {right}")
    order = LimitOrder("SELL", qty, limit_price) if limit_price else MarketOrder("SELL", qty)
    trade = ib.placeOrder(contract, order)
    ib.sleep(1)
    label = f"{right.upper()} {strike} exp={expiry}"
    print(f"SELL OPTION {qty}x {symbol} {label}  status={trade.orderStatus.status}")
    return trade


def cancel_order(ib: IB, trade: Trade) -> None:
    ib.cancelOrder(trade.order)
    ib.sleep(1)
    print(f"Cancelled order {trade.order.orderId}  status={trade.orderStatus.status}")


def print_open_orders(ib: IB) -> None:
    trades = ib.openTrades()
    if not trades:
        print("\nNo open orders")
        return
    rows = [
        {
            "id": t.order.orderId,
            "symbol": t.contract.symbol,
            "action": t.order.action,
            "type": t.order.orderType,
            "qty": t.order.totalQuantity,
            "price": getattr(t.order, "lmtPrice", "—"),
            "status": t.orderStatus.status,
        }
        for t in trades
    ]
    print("\n--- Open Orders ---")
    print(pd.DataFrame(rows).to_string(index=False))


# ---------------------------------------------------------------------------
# Interactive CLI
# ---------------------------------------------------------------------------

HELP = """
Commands:
  account               — show account summary
  positions             — show open positions
  orders                — show open orders
  quote   <SYM>         — live quote
  history <SYM> [dur] [bar]  — OHLCV bars  (default: 5 D / 1 hour)

  --- Stocks ---
  buy     <SYM> <qty>              — market buy
  sell    <SYM> <qty>              — market sell
  blimit  <SYM> <qty> <price>      — limit buy
  slimit  <SYM> <qty> <price>      — limit sell
  bracket <SYM> <qty> <entry> <tp> <sl>  — bracket order

  --- Options ---
  chain   <SYM>                    — show option chain (expirations + strikes)
  buyopt  <SYM> <YYYYMMDD> <strike> <C|P> <qty> [limit_price]
  sellopt <SYM> <YYYYMMDD> <strike> <C|P> <qty> [limit_price]
  Example: buyopt AAPL 20260619 200 C 1 3.50

  cancel  <order_id>    — cancel an open order by ID
  help                  — this message
  exit / quit           — disconnect and quit
"""


def run_cli(ib: IB) -> None:
    print(HELP)
    active_trades: dict[int, Trade] = {}

    while True:
        try:
            raw = input("trader> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue

        parts = raw.split()
        cmd, args = parts[0].lower(), parts[1:]

        try:
            if cmd in ("exit", "quit"):
                break

            elif cmd == "help":
                print(HELP)

            elif cmd == "account":
                print_account(ib)

            elif cmd == "positions":
                print_positions(ib)

            elif cmd == "orders":
                print_open_orders(ib)
                active_trades = {t.order.orderId: t for t in ib.openTrades()}

            elif cmd == "quote":
                if len(args) < 1:
                    print("Usage: quote <SYM>")
                    continue
                q = get_quote(ib, args[0].upper())
                print(f"\n{q.symbol}  bid={q.bid}  ask={q.ask}  last={q.last}  vol={q.volume}")

            elif cmd == "history":
                sym = args[0].upper() if args else None
                if not sym:
                    print("Usage: history <SYM> [duration] [bar_size]")
                    continue
                dur = args[1] if len(args) > 1 else "5 D"
                bar = args[2].replace("_", " ") if len(args) > 2 else "1 hour"
                df = get_historical_bars(ib, sym, duration=dur, bar_size=bar)
                print(f"\n{sym} — {dur} @ {bar}")
                print(df.tail(10).to_string(index=False))

            elif cmd == "buy":
                sym, qty = args[0].upper(), int(args[1])
                t = buy_market(ib, sym, qty)
                active_trades[t.order.orderId] = t

            elif cmd == "sell":
                sym, qty = args[0].upper(), int(args[1])
                t = sell_market(ib, sym, qty)
                active_trades[t.order.orderId] = t

            elif cmd == "blimit":
                sym, qty, price = args[0].upper(), int(args[1]), float(args[2])
                t = buy_limit(ib, sym, qty, price)
                active_trades[t.order.orderId] = t

            elif cmd == "slimit":
                sym, qty, price = args[0].upper(), int(args[1]), float(args[2])
                t = sell_limit(ib, sym, qty, price)
                active_trades[t.order.orderId] = t

            elif cmd == "bracket":
                sym = args[0].upper()
                qty, entry, tp, sl = int(args[1]), float(args[2]), float(args[3]), float(args[4])
                trades = buy_bracket(ib, sym, qty, entry, tp, sl)
                for t in trades:
                    active_trades[t.order.orderId] = t

            elif cmd == "chain":
                if len(args) < 1:
                    print("Usage: chain <SYM>")
                    continue
                get_option_chain(ib, args[0].upper())

            elif cmd == "buyopt":
                # buyopt AAPL 20260619 200 C 1 [3.50]
                sym, expiry, strike, right, qty = args[0].upper(), args[1], float(args[2]), args[3], int(args[4])
                price = float(args[5]) if len(args) > 5 else None
                t = buy_option(ib, sym, expiry, strike, right, qty, price)
                active_trades[t.order.orderId] = t

            elif cmd == "sellopt":
                sym, expiry, strike, right, qty = args[0].upper(), args[1], float(args[2]), args[3], int(args[4])
                price = float(args[5]) if len(args) > 5 else None
                t = sell_option(ib, sym, expiry, strike, right, qty, price)
                active_trades[t.order.orderId] = t

            elif cmd == "cancel":
                oid = int(args[0])
                if oid in active_trades:
                    cancel_order(ib, active_trades[oid])
                else:
                    # refresh from open trades
                    active_trades = {t.order.orderId: t for t in ib.openTrades()}
                    if oid in active_trades:
                        cancel_order(ib, active_trades[oid])
                    else:
                        print(f"Order {oid} not found in open orders")

            else:
                print(f"Unknown command '{cmd}'. Type 'help' for options.")

        except (IndexError, ValueError):
            print("Bad arguments — type 'help' for usage")
        except Exception as exc:
            print(f"Error: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ib = connect()
    try:
        run_cli(ib)
    finally:
        disconnect(ib)


if __name__ == "__main__":
    main()
