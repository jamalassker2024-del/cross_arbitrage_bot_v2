#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-EXCHANGE ARBITRAGE – BINANCE SPOT (bookTicker) vs BYBIT LINEAR
- Binance: wss://stream.binance.com:9443/ws/{symbol}@bookTicker (fastest)
- Bybit: wss://stream.bybit.com/v5/public/linear (orderbook.50)
- Detects price jumps and executes arbitrage
"""

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["btcusdt", "ethusdt", "solusdt", "pepeusdt", "dogeusdt"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "PRICE_JUMP_BPS": Decimal("3"),              # 0.03% jump threshold
    "TOTAL_FEES_BPS": Decimal("10.5"),           # 0.105% combined fees
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class CrossArbitrageBot:
    def __init__(self):
        self.binance_prices = {}
        self.binance_asks = {}
        self.binance_bids = {}
        self.bybit_prices = {}
        self.bybit_asks = {}
        self.bybit_bids = {}
        self.last_binance_time = {}
        self.last_bybit_time = {}
        self.running = True
        self.last_price_print = 0
        self.price_history = {}  # Track price changes

    async def subscribe_binance_spot(self, symbol):
        """Binance Spot – bookTicker stream (fastest, L1 data)"""
        stream = f"{symbol}@bookTicker"
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"✅ Binance Spot {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        ask_price = Decimal(data['a'])
                        bid_price = Decimal(data['b'])
                        mid_price = (ask_price + bid_price) / 2
                        
                        # Store old price for jump detection
                        old_price = self.binance_prices.get(symbol, mid_price)
                        self.binance_prices[symbol] = mid_price
                        self.binance_asks[symbol] = ask_price
                        self.binance_bids[symbol] = bid_price
                        self.last_binance_time[symbol] = time.time()
                        
                        # Detect price jump
                        if old_price > 0:
                            change_bps = abs(mid_price - old_price) / old_price * 10000
                            direction = "UP" if mid_price > old_price else "DOWN"
                            
                            if change_bps >= CONFIG["PRICE_JUMP_BPS"]:
                                print(f"📈 BINANCE {symbol.upper()}: {old_price:.8f} → {mid_price:.8f} ({direction} {change_bps:.2f}bps)")
                                
                                # Check arbitrage opportunity
                                if symbol in self.bybit_prices:
                                    bybit_price = self.bybit_prices[symbol]
                                    if bybit_price > 0:
                                        spread_bps = abs(mid_price - bybit_price) / min(mid_price, bybit_price) * 10000
                                        if spread_bps > CONFIG["TOTAL_FEES_BPS"]:
                                            if mid_price > bybit_price:
                                                print(f"🎯 ARBITRAGE! Binance HIGHER ({mid_price:.8f}) | Bybit LOWER ({bybit_price:.8f}) | Spread={spread_bps:.2f}bps")
                                                print(f"   → BUY Bybit @ {bybit_price:.8f} | SELL Binance @ {mid_price:.8f}")
                                            else:
                                                print(f"🎯 ARBITRAGE! Binance LOWER ({mid_price:.8f}) | Bybit HIGHER ({bybit_price:.8f}) | Spread={spread_bps:.2f}bps")
                                                print(f"   → BUY Binance @ {mid_price:.8f} | SELL Bybit @ {bybit_price:.8f}")
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def subscribe_bybit_linear(self, symbol):
        """Bybit Linear – orderbook.50 stream"""
        while self.running:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.50.{symbol.upper()}"]}
                    await ws.send(json.dumps(subscribe_msg))
                    print(f"✅ Bybit Linear {symbol.upper()} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'topic' in data and 'data' in data:
                            book_data = data['data']
                            if 'b' in book_data and 'a' in book_data and book_data['b'] and book_data['a']:
                                best_bid = Decimal(book_data['b'][0][0])
                                best_ask = Decimal(book_data['a'][0][0])
                                mid_price = (best_bid + best_ask) / 2
                                self.bybit_prices[symbol] = mid_price
                                self.bybit_bids[symbol] = best_bid
                                self.bybit_asks[symbol] = best_ask
                                self.last_bybit_time[symbol] = time.time()
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def run(self):
        # Start WebSocket connections
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_binance_spot(sym))
            asyncio.create_task(self.subscribe_bybit_linear(sym))

        print("\n" + "="*60)
        print("🚀 CROSS-EXCHANGE ARBITRAGE BOT")
        print("="*60)
        print(f"   Binance Spot (bookTicker) – fastest L1 data")
        print(f"   Bybit Linear (orderbook.50)")
        print(f"   Price jump threshold: {CONFIG['PRICE_JUMP_BPS']}bps")
        print(f"   Min arbitrage spread: {CONFIG['TOTAL_FEES_BPS']}bps")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print("="*60 + "\n")

        while self.running:
            now = time.time()
            
            # Print current prices every 5 seconds
            if now - self.last_price_print > 5:
                print("\n📊 LIVE PRICES:")
                for sym in CONFIG["SYMBOLS"]:
                    binance = self.binance_prices.get(sym, 0)
                    bybit = self.bybit_prices.get(sym, 0)
                    binance_age = now - self.last_binance_time.get(sym, now) if sym in self.last_binance_time else 999
                    bybit_age = now - self.last_bybit_time.get(sym, now) if sym in self.last_bybit_time else 999
                    
                    if binance > 0 and bybit > 0:
                        spread = abs(binance - bybit) / min(binance, bybit) * 10000
                        print(f"   {sym.upper()}: Bin={binance:.8f} ({binance_age:.1f}s) | Byb={bybit:.8f} ({bybit_age:.1f}s) | Spread={spread:.2f}bps")
                    else:
                        print(f"   {sym.upper()}: Bin={binance:.8f} | Byb={bybit:.8f} (waiting for data)")
                self.last_price_print = now

            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(CrossArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
