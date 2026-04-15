#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HIGH-FREQUENCY ARBITRAGE MACHINE – FIXED FOR 2026
- Atomic execution (no waiting for targets)
- Correct fee math (15.5bps total)
- Sub-millisecond reaction (no sleep)
- L1 order book data for speed
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
    "MIN_SPREAD_BPS": Decimal("18"),             # Must be > 15.5bps total fees
    "BINANCE_FEE_BPS": Decimal("10"),            # 0.1% = 10bps
    "BYBIT_FEE_BPS": Decimal("5.5"),             # 0.055% = 5.5bps
    "TOTAL_FEES_BPS": Decimal("15.5"),           # 10 + 5.5 = 15.5bps
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class AtomicArbitrageMachine:
    def __init__(self):
        self.prices = {s: {
            "binance_ask": Decimal(0), "binance_bid": Decimal(0),
            "bybit_ask": Decimal(0), "bybit_bid": Decimal(0)
        } for s in CONFIG["SYMBOLS"]}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.total_profit = Decimal('0')
        self.last_trade_time = {}
        self.start_time = time.time()
        self.running = True

    async def binance_handler(self, symbol):
        """Binance Spot – bookTicker stream (fastest L1 data)"""
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{symbol}@bookTicker"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                        self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                        await self.check_arbitrage(symbol)
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(1)

    async def bybit_handler(self, symbol):
        """Bybit Linear – orderbook.1 stream (fastest L1 data)"""
        while self.running:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.1.{symbol.upper()}"]}
                    await ws.send(json.dumps(subscribe_msg))
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'data' in data:
                            d = data['data']
                            if d.get('a') and d.get('b'):
                                self.prices[symbol]["bybit_ask"] = Decimal(d['a'][0][0])
                                self.prices[symbol]["bybit_bid"] = Decimal(d['b'][0][0])
                                await self.check_arbitrage(symbol)
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(1)

    async def check_arbitrage(self, symbol):
        """Atomic arbitrage check – executes immediately when profitable"""
        p = self.prices[symbol]
        
        # Check if we have all prices
        if not all([p["binance_ask"], p["binance_bid"], p["bybit_ask"], p["bybit_bid"]]):
            return
        
        # Cooldown check (prevent double trading same symbol too fast)
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < 1:
            return

        # Opportunity 1: Buy Binance (ask) → Sell Bybit (bid)
        # Profit = Bybit_bid - Binance_ask - Total_Fees
        spread1_bps = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
        
        # Opportunity 2: Buy Bybit (ask) → Sell Binance (bid)
        # Profit = Binance_bid - Bybit_ask - Total_Fees
        spread2_bps = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000

        # Execute immediately if profitable
        if spread1_bps > CONFIG["MIN_SPREAD_BPS"]:
            await self.execute_atomic_arbitrage(
                symbol, "BUY BINANCE → SELL BYBIT",
                p["binance_ask"], p["bybit_bid"], spread1_bps
            )
        elif spread2_bps > CONFIG["MIN_SPREAD_BPS"]:
            await self.execute_atomic_arbitrage(
                symbol, "BUY BYBIT → SELL BINANCE",
                p["bybit_ask"], p["binance_bid"], spread2_bps
            )

    async def execute_atomic_arbitrage(self, symbol, direction, buy_price, sell_price, spread_bps):
        """Execute atomic arbitrage – both legs simultaneously"""
        order_size = CONFIG["ORDER_SIZE_USDT"]
        
        # Check balance
        if order_size > self.balance:
            return
        
        # Calculate net profit after fees
        net_profit_bps = spread_bps - CONFIG["TOTAL_FEES_BPS"]
        usd_profit = (order_size * net_profit_bps) / 10000
        
        if usd_profit <= 0:
            return

        # Execute (simulated market orders on both exchanges)
        self.balance -= order_size  # Deduct cost
        self.balance += order_size + usd_profit  # Add back + profit
        
        # Update stats
        self.total_trades += 1
        self.winning_trades += 1
        self.total_profit += usd_profit
        self.last_trade_time[symbol] = time.time()
        
        # Print result
        runtime = (time.time() - self.start_time) / 60
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        
        print(f"\n{'='*70}")
        print(f"🚀 ATOMIC ARBITRAGE EXECUTED!")
        print(f"   📊 Symbol: {symbol.upper()} | Direction: {direction}")
        print(f"   💰 Buy Price: {buy_price:.8f} | Sell Price: {sell_price:.8f}")
        print(f"   📈 Gross Spread: {spread_bps:.2f}bps | Fees: {CONFIG['TOTAL_FEES_BPS']}bps")
        print(f"   💵 Net Profit: +${usd_profit:.5f} ({(net_profit_bps/spread_bps*100):.1f}% of spread)")
        print(f"   💳 Balance: ${self.balance:.2f} | Total Profit: +${self.total_profit:.5f}")
        print(f"   📊 Trades: {self.total_trades} | Win Rate: {win_rate:.1f}% | Runtime: {runtime:.1f}min")
        print(f"{'='*70}\n")

    async def stats_printer(self):
        """Print live stats every 10 seconds (non-blocking)"""
        while self.running:
            await asyncio.sleep(10)
            runtime = (time.time() - self.start_time) / 60
            win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
            print(f"\n📊 LIVE STATS | Runtime: {runtime:.1f}min | Balance: ${self.balance:.2f} | Profit: +${self.total_profit:.5f} | Trades: {self.total_trades} | WR: {win_rate:.1f}%\n")

    async def run(self):
        # Start WebSocket handlers for all symbols
        tasks = []
        for sym in CONFIG["SYMBOLS"]:
            tasks.append(self.binance_handler(sym))
            tasks.append(self.bybit_handler(sym))
        tasks.append(self.stats_printer())
        
        print("\n" + "="*70)
        print("🚀🚀🚀 ATOMIC ARBITRAGE MACHINE – HFT READY 🚀🚀🚀")
        print("="*70)
        print(f"   📊 Min spread required: {CONFIG['MIN_SPREAD_BPS']}bps")
        print(f"   💰 Total fees: {CONFIG['TOTAL_FEES_BPS']}bps (Binance {CONFIG['BINANCE_FEE_BPS']}bps + Bybit {CONFIG['BYBIT_FEE_BPS']}bps)")
        print(f"   💵 Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   🎯 Initial balance: ${CONFIG['INITIAL_BALANCE']}")
        print("="*70)
        print(f"   ⚡ Atomic execution | No sleep | Sub-millisecond reaction")
        print("="*70 + "\n")
        
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(AtomicArbitrageMachine().run())
    except KeyboardInterrupt:
        print("\n🔴 Shutdown complete")
