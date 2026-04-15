#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-EXCHANGE PRICE JUMP ARBITRAGE – DEBUG VERSION
- Shows live Binance and Bybit prices
- Lower price jump threshold (2bps)
- Prints price changes immediately
"""

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "PRICE_JUMP_BPS": Decimal("2"),              # 0.02% – lower threshold
    "TOTAL_FEES_BPS": Decimal("10.5"),           # 0.105% combined fees
    "BINANCE_FUTURES_WS": "wss://fstream.binance.com/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class DebugArbitrageBot:
    def __init__(self):
        self.binance_prices = {}
        self.bybit_prices = {}
        self.binance_old_prices = {}
        self.last_binance_time = {}
        self.last_bybit_time = {}
        self.running = True
        self.last_price_print = 0

    async def subscribe_binance_futures(self, symbol):
        """Binance Futures WebSocket"""
        stream = f"{symbol.lower()}@depth20@100ms"
        url = f"{CONFIG['BINANCE_FUTURES_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        
                        # Extract mid price from depth
                        if 'bids' in data and 'asks' in data and data['bids'] and data['asks']:
                            best_bid = Decimal(data['bids'][0][0])
                            best_ask = Decimal(data['asks'][0][0])
                            mid_price = (best_bid + best_ask) / 2
                            
                            # Store old price for jump detection
                            old_price = self.binance_prices.get(symbol, mid_price)
                            self.binance_old_prices[symbol] = old_price
                            self.binance_prices[symbol] = mid_price
                            self.last_binance_time[symbol] = time.time()
                            
                            # Detect price jump
                            if old_price > 0:
                                change_bps = abs(mid_price - old_price) / old_price * 10000
                                direction = "UP" if mid_price > old_price else "DOWN"
                                
                                if change_bps >= CONFIG["PRICE_JUMP_BPS"]:
                                    print(f"📈 BINANCE {symbol}: {old_price:.8f} → {mid_price:.8f} ({direction} {change_bps:.2f}bps)")
                                    
                                    # Check if Bybit lags
                                    if symbol in self.bybit_prices:
                                        bybit_price = self.bybit_prices[symbol]
                                        spread_bps = abs(mid_price - bybit_price) / min(mid_price, bybit_price) * 10000
                                        if spread_bps > CONFIG["TOTAL_FEES_BPS"]:
                                            print(f"🎯 ARBITRAGE OPPORTUNITY! Spread={spread_bps:.2f}bps > fees={CONFIG['TOTAL_FEES_BPS']}bps")
                                            print(f"   Buy on Bybit @ {bybit_price:.8f} | Sell on Binance @ {mid_price:.8f}")
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    async def subscribe_bybit_linear(self, symbol):
        """Bybit Linear WebSocket"""
        while self.running:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.50.{symbol}"]}
                    await ws.send(json.dumps(subscribe_msg))
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'topic' in data and 'data' in data:
                            book_data = data['data']
                            if 'b' in book_data and 'a' in book_data and book_data['b'] and book_data['a']:
                                best_bid = Decimal(book_data['b'][0][0])
                                best_ask = Decimal(book_data['a'][0][0])
                                mid_price = (best_bid + best_ask) / 2
                                self.bybit_prices[symbol] = mid_price
                                self.last_bybit_time[symbol] = time.time()
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    async def run(self):
        # Start WebSocket connections
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_binance_futures(sym))
            asyncio.create_task(self.subscribe_bybit_linear(sym))

        print("\n" + "="*60)
        print("🔍 DEBUG ARBITRAGE BOT – LIVE PRICES")
        print("="*60)
        print(f"   Price jump threshold: {CONFIG['PRICE_JUMP_BPS']}bps")
        print(f"   Min arbitrage spread: {CONFIG['TOTAL_FEES_BPS']}bps")
        print("="*60 + "\n")

        while self.running:
            now = time.time()
            
            # Print current prices every 5 seconds
            if now - self.last_price_print > 5:
                print("\n📊 CURRENT PRICES:")
                for sym in CONFIG["SYMBOLS"]:
                    binance = self.binance_prices.get(sym, 0)
                    bybit = self.bybit_prices.get(sym, 0)
                    binance_age = now - self.last_binance_time.get(sym, now) if sym in self.last_binance_time else 999
                    bybit_age = now - self.last_bybit_time.get(sym, now) if sym in self.last_bybit_time else 999
                    
                    if binance > 0 and bybit > 0:
                        spread = abs(binance - bybit) / min(binance, bybit) * 10000
                        print(f"   {sym}: Bin={binance:.8f} ({binance_age:.1f}s) | Byb={bybit:.8f} ({bybit_age:.1f}s) | Spread={spread:.2f}bps")
                    else:
                        print(f"   {sym}: Bin={binance:.8f} | Byb={bybit:.8f} (waiting for data)")
                self.last_price_print = now

            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(DebugArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
