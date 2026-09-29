#!/usr/bin/env python3
"""
Emergency flatten script — closes all SPY option positions in the account.

Uses SMART exchange explicitly (Error 321 fix) and qualifies contracts first.
Paper account by default (port 4002).
"""
import sys
import time
from ib_insync import IB, Option, Stock, MarketOrder

HOST = '127.0.0.1'
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4002
CLIENT_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 99

ib = IB()
ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=False)
print(f"Connected to IB Gateway/TWS at {HOST}:{PORT} (clientId={CLIENT_ID})")

positions = [p for p in ib.positions() if 'SPY' in p.contract.symbol and p.position != 0]
if not positions:
    print("No SPY positions to flatten.")
    ib.disconnect()
    sys.exit(0)

print(f"\nFound {len(positions)} SPY position(s) to flatten:")
for p in positions:
    c = p.contract
    print(f"  {c.localSymbol}  pos={p.position:+.0f}  avgCost=${p.avgCost:.4f}  ({c.secType})")

trades = []
for p in positions:
    c = p.contract
    action = 'BUY' if p.position < 0 else 'SELL'
    qty = abs(p.position)

    if c.secType == 'OPT':
        k = Option(
            symbol=c.symbol,
            lastTradeDateOrContractMonth=c.lastTradeDateOrContractMonth,
            strike=c.strike,
            right=c.right,
            multiplier=c.multiplier or '100',
            currency=c.currency or 'USD',
            exchange='SMART',
            localSymbol=c.localSymbol,
            tradingClass=c.tradingClass or 'SPY',
        )
    else:
        k = Stock(c.symbol, 'SMART', c.currency or 'USD')
    k.includeExpired = True

    qualified = ib.qualifyContracts(k)
    if not qualified:
        print(f"  !! Failed to qualify {c.localSymbol}, skipping")
        continue
    k = qualified[0]

    order = MarketOrder(action, qty, tif='DAY')
    trade = ib.placeOrder(k, order)
    trades.append(trade)
    print(f"  -> Submitted {action} {qty:.0f}x {k.localSymbol} conId={k.conId}")

print("\nWaiting for fills (15s)...")
ib.sleep(15)

for t in trades:
    st = t.orderStatus
    print(f"  {t.contract.localSymbol}: {st.status} filled={st.filled} remaining={st.remaining} avgFill={st.avgFillPrice}")

remaining = [p for p in ib.positions() if 'SPY' in p.contract.symbol and p.position != 0]
if remaining:
    print(f"\n{len(remaining)} position(s) still open after wait:")
    for p in remaining:
        print(f"  {p.contract.localSymbol} pos={p.position:+.0f}")
else:
    print("\nAll SPY positions flat.")

ib.disconnect()
