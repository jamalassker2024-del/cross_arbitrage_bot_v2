import asyncio
import json
import os
import sys
import websockets
from decimal import Decimal
from collections import deque

# --- CONFIGURATION ---
SYMBOL = os.getenv("SYMBOL", "btcusdt")
TRADE_SIZE = Decimal(os.getenv("TRADE_SIZE", "1000")) 
ENTRY_THRESHOLD = Decimal("0.25")
VELOCITY_THRESHOLD = Decimal("0.05")
FEE = Decimal("0.0004")
TARGET_PROFIT = Decimal("0.0030") # 0.3%
STOP_LOSS = Decimal("-0.0045")    # 0.45%

class SentinelCloud:
    def __init__(self):
        self.balance = Decimal("5000.00") # Virtual Demo Balance
        self.active_trade = None
        self.imb_history = deque(maxlen=3)

    def get_weighted_imb(self, bids, asks):
        # Weighting Level 1 much higher than Level 20
        b_w = sum((Decimal(b[0]) * Decimal(b[1])) * (Decimal(1) / (i + 1)) for i, b in enumerate(bids[:10]))
        a_w = sum((Decimal(a[0]) * Decimal(a[1])) * (Decimal(1) / (i + 1)) for i, a in enumerate(asks[:10]))
        return (b_w - a_w) / (b_w + a_w)

    async def start(self):
        url = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth20@100ms"
        print(f"🚀 Sentinel Cloud Active: Monitoring {SYMBOL.upper()}")
        
        async with websockets.connect(url) as ws:
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    curr_imb = self.get_weighted_imb(bids, asks)
                    self.imb_history.append(curr_imb)
                    velocity = curr_imb - self.imb_history[0] if len(self.imb_history) > 1 else 0

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        net_roi = roi - (FEE * 2)

                        if net_roi >= TARGET_PROFIT or net_roi <= STOP_LOSS:
                            pnl = TRADE_SIZE * net_roi
                            self.balance += pnl
                            print(f"✅ CLOSED {t['side']} | PnL: ${round(pnl, 2)} | New Balance: ${round(self.balance, 2)}")
                            self.active_trade = None
                    else:
                        if curr_imb > ENTRY_THRESHOLD and velocity > VELOCITY_THRESHOLD:
                            self.active_trade = {"side": "BUY", "entry": ask_p}
                            print(f"📡 LONG ENTERED @ {ask_p} | Imb: {round(curr_imb, 2)}")
                        elif curr_imb < -ENTRY_THRESHOLD and velocity < -VELOCITY_THRESHOLD:
                            self.active_trade = {"side": "SELL", "entry": bid_p}
                            print(f"📡 SHORT ENTERED @ {bid_p} | Imb: {round(curr_imb, 2)}")

                except Exception as e:
                    print(f"Error: {e}")
                    await asyncio.sleep(5)

if __name__ == "__main__":
    bot = SentinelCloud()
    asyncio.run(bot.start())
