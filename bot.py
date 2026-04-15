#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ATOMIC ARBITRAGE MACHINE – FIXED FOR 2026
- Sticky prices (keeps last known value when only one side updates)
- Proper None-check (never trades on zero/unknown prices)
- Heartbeat so you know it's alive
- Lower cooldown for faster re-entry
"""

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["btcusdt", "ethusdt", "solusdt", "pepeusdt", "dogeusdt"],
    "ORDER_SIZE_USDT": Decimal("10.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("18"),
    "TOTAL_FEES_BPS": Decimal("15.5"),
    "COOLDOWN_SEC": 0.5,
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class AtomicArbitrageMachine:
    def __init__(self):
        # Initialize with None to strictly verify data presence
        self.prices = {s: {
            "binance_ask": None, "binance_bid": None,
            "bybit_ask": None, "bybit_bid": None
        } for s in CONFIG["SYMBOLS"]}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.total_profit = Decimal('0')
        self.last_trade_time = {}
        self.start_time = time.time()

    async def binance_handler(self, symbol):
        """Binance Spot – bookTicker stream (always sends both sides)"""
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{symbol.lower()}@bookTicker"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                        self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                        await self.check_arbitrage(symbol)
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(2)

    async def bybit_handler(self, symbol):
        """Bybit Linear – orderbook.1 stream (delta updates, only one side at a time)"""
        while True:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    # Subscribe to orderbook
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.1.{symbol.upper()}"]}
                    await ws.send(json.dumps(subscribe_msg))
                    
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'data' in data:
                            d = data['data']
                            # FIX: Update only what is provided, keep the other side 'sticky'
                            if 'a' in d and d['a']:
                                self.prices[symbol]["bybit_ask"] = Decimal(d['a'][0][0])
                            if 'b' in d and d['b']:
                                self.prices[symbol]["bybit_bid"] = Decimal(d['b'][0][0])
                            await self.check_arbitrage(symbol)
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(2)

    async def check_arbitrage(self, symbol):
        """Check and execute arbitrage immediately when profitable"""
        p = self.prices[symbol]
        
        # Ensure all 4 price points exist before calculating
        if any(v is None for v in p.values()):
            return
        
        # Cooldown to prevent double-trading same symbol too fast
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return

        # Opportunity 1: Buy Binance (ask) → Sell Bybit (bid)
        spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
        
        # Opportunity 2: Buy Bybit (ask) → Sell Binance (bid)
        spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000

        # Execute immediately if profitable
        if spread1 > CONFIG["MIN_SPREAD_BPS"]:
            await self.execute_trade(symbol, "BIN→BYB", p["binance_ask"], p["bybit_bid"], spread1)
        elif spread2 > CONFIG["MIN_SPREAD_BPS"]:
            await self.execute_trade(symbol, "BYB→BIN", p["bybit_ask"], p["binance_bid"], spread2)

    async def execute_trade(self, symbol, direction, buy_price, sell_price, spread_bps):
        """Execute atomic arbitrage trade"""
        order_size = CONFIG["ORDER_SIZE_USDT"]
        
        # Check balance
        if order_size > self.balance:
            return
        
        # Calculate net profit after fees
        net_bps = spread_bps - CONFIG["TOTAL_FEES_BPS"]
        profit = (order_size * net_bps) / 10000
        
        if profit <= 0:
            return

        # Execute (simulated market orders on both exchanges)
        self.balance += profit
        self.total_trades += 1
        self.total_profit += profit
        self.last_trade_time[symbol] = time.time()
        
        # Print execution result
        runtime = int(time.time() - self.start_time)
        print(f"\n💰💰💰 ARBITRAGE! {symbol.upper()} | {direction}")
        print(f"   Spread: {spread_bps:.1f}bps | Net Profit: +${profit:.5f}")
        print(f"   Balance: ${self.balance:.2f} | Total Profit: +${self.total_profit:.5f}")
        print(f"   Trades: {self.total_trades} | Runtime: {runtime}s\n")

    async def heartbeat(self):
        """Show bot is alive even when no trades happen"""
        while True:
            await asyncio.sleep(15)
            runtime = int(time.time() - self.start_time)
            print(f"💓 HEARTBEAT | Runtime: {runtime}s | Balance: ${self.balance:.2f} | Trades: {self.total_trades} | Profit: +${self.total_profit:.5f}")

    async def run(self):
        # Start all handlers
        tasks = []
        for sym in CONFIG["SYMBOLS"]:
            tasks.append(self.binance_handler(sym))
            tasks.append(self.bybit_handler(sym))
        tasks.append(self.heartbeat())
        
        print("\n" + "="*60)
        print("🚀 ATOMIC ARBITRAGE MACHINE – READY")
        print("="*60)
        print(f"   Symbols: {', '.join(CONFIG['SYMBOLS'])}")
        print(f"   Min spread: {CONFIG['MIN_SPREAD_BPS']}bps | Fees: {CONFIG['TOTAL_FEES_BPS']}bps")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']} | Balance: ${self.balance}")
        print("="*60 + "\n")
        
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(AtomicArbitrageMachine().run())
    except KeyboardInterrupt:
        print("\n🔴 Shutdown complete")
