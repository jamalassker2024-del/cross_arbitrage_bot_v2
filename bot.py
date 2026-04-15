#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-ARBITRAGE BOT – WITH DATA VERIFICATION
- First verifies WebSocket connections are receiving data
- Prints live spreads every 2 seconds
- Only trades when both exchanges have fresh data
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT", "SUIUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("8"),            # 0.08% minimum spread
    "BINANCE_FEE": Decimal("0.001"),
    "BYBIT_FEE": Decimal("0.001"),
    "SLIPPAGE_BPS": Decimal("2"),
    "TAKE_PROFIT_BPS": Decimal("4"),
    "STOP_LOSS_BPS": Decimal("6"),
    "MAX_HOLD_SECONDS": 5,
    "COOLDOWN_SEC": 2,
    "SCAN_INTERVAL_MS": 100,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_WS": "wss://stream.bybit.com/v5/public/spot",
}

TAKER_FEE = Decimal("0.001")
SLIPPAGE_FACTOR = Decimal("1") + CONFIG["SLIPPAGE_BPS"] / Decimal("10000")

class CrossArbitrageBot:
    def __init__(self):
        self.prices = {s: {"binance_ask": None, "binance_bid": None,
                           "bybit_ask": None, "bybit_bid": None,
                           "binance_time": 0, "bybit_time": 0}
                       for s in CONFIG["SYMBOLS"]}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.running = True
        self.data_received = {s: {"binance": False, "bybit": False} for s in CONFIG["SYMBOLS"]}

    # ---------- WebSocket Feeds ----------
    async def binance_stream(self, symbol):
        """Binance bookTicker stream"""
        stream = f"{symbol.lower()}@bookTicker"
        url = f"{CONFIG['BINANCE_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"✅ Binance {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                        self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                        self.prices[symbol]["binance_time"] = time.time()
                        self.data_received[symbol]["binance"] = True
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    async def bybit_stream(self):
        """Bybit tickers stream (all symbols)"""
        url = CONFIG["BYBIT_WS"]
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    print("✅ Bybit connected")
                    sub_msg = {"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}
                    await ws.send(json.dumps(sub_msg))
                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("op") == "subscribe":
                            continue
                        if "topic" in data and data["topic"].startswith("tickers."):
                            d = data.get("data", {})
                            sym = d.get('symbol')
                            if sym in self.prices:
                                if 'ask1Price' in d:
                                    self.prices[sym]["bybit_ask"] = Decimal(d['ask1Price'])
                                    self.data_received[sym]["bybit"] = True
                                if 'bid1Price' in d:
                                    self.prices[sym]["bybit_bid"] = Decimal(d['bid1Price'])
                                self.prices[sym]["bybit_time"] = time.time()
            except Exception as e:
                print(f"⚠️ Bybit error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    # ---------- Data Freshness ----------
    def is_fresh(self, sym, max_age=2.0):
        """Both exchanges must have recent data"""
        p = self.prices[sym]
        now = time.time()
        binance_fresh = p["binance_ask"] and p["binance_bid"] and (now - p["binance_time"] < max_age)
        bybit_fresh = p["bybit_ask"] and p["bybit_bid"] and (now - p["bybit_time"] < max_age)
        return binance_fresh and bybit_fresh

    # ---------- Arbitrage Execution ----------
    def open_arbitrage(self, symbol, direction, buy_exch, sell_exch, buy_price, sell_price):
        if buy_price <= 0 or sell_price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / buy_price
        buy_exec = buy_price * SLIPPAGE_FACTOR
        sell_exec = sell_price / SLIPPAGE_FACTOR

        cost_buy = qty * buy_exec
        fee_buy = cost_buy * TAKER_FEE
        gross_sell = qty * sell_exec
        fee_sell = gross_sell * TAKER_FEE

        total_cost = cost_buy + fee_buy
        total_return = gross_sell - fee_sell

        if total_cost > self.balance:
            return False

        self.balance -= total_cost
        expected_profit = total_return - total_cost

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'entry_time': time.time(),
            'order_size': order_size,
            'expected_profit': expected_profit,
            'entry_spread': (sell_price - buy_price) / buy_price * 10000
        }

        print(f"🔄 ARBITRAGE OPEN {symbol} | {direction} | Spread: {self.positions[symbol]['entry_spread']:.2f}bps | Expected: +${expected_profit:.5f}")
        return True

    def check_and_close_arbitrage(self, symbol):
        pos = self.positions.get(symbol)
        if not pos:
            return

        if not self.is_fresh(symbol):
            return

        p = self.prices[symbol]
        now = time.time()

        if pos['direction'] == 'binance_buy_bybit_sell':
            exit_binance = p["binance_bid"]
            exit_bybit = p["bybit_ask"]
        else:
            exit_binance = p["bybit_bid"]
            exit_bybit = p["binance_ask"]

        if exit_binance <= 0 or exit_bybit <= 0:
            return

        qty = pos['quantity']
        if pos['direction'] == 'binance_buy_bybit_sell':
            gross_return = qty * exit_binance
            cost_close = qty * exit_bybit
        else:
            gross_return = qty * exit_bybit
            cost_close = qty * exit_binance

        fee_exit = (gross_return + cost_close) * TAKER_FEE
        profit = (gross_return - cost_close) - fee_exit

        if profit >= CONFIG["ORDER_SIZE_USDT"] * CONFIG["TAKE_PROFIT_BPS"] / 10000:
            self.close_arbitrage(symbol, profit, "TAKE_PROFIT")
        elif profit <= -CONFIG["ORDER_SIZE_USDT"] * CONFIG["STOP_LOSS_BPS"] / 10000:
            self.close_arbitrage(symbol, profit, "STOP_LOSS")
        elif now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
            self.close_arbitrage(symbol, profit, "TIMEOUT")

    def close_arbitrage(self, symbol, profit, reason):
        pos = self.positions.pop(symbol)
        self.balance += pos['order_size'] + profit
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅ WIN' if profit>0 else '❌ LOSS'} {reason} {symbol} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    # ---------- Main Loop with Data Verification ----------
    async def run(self):
        # Start WebSocket streams
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.binance_stream(sym))
        asyncio.create_task(self.bybit_stream())

        print("\n" + "="*60)
        print("🚀 CROSS-ARBITRAGE BOT – DATA VERIFICATION MODE")
        print("="*60)
        print(f"   Monitoring {len(CONFIG['SYMBOLS'])} pairs: {', '.join(CONFIG['SYMBOLS'])}")
        print(f"   Min spread required: {float(CONFIG['MIN_SPREAD_BPS'])/100:.2f}%")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print("\n⏳ Waiting for data from exchanges...\n")

        # Wait for initial data (max 10 seconds)
        await asyncio.sleep(10)

        # Check data status
        print("\n📊 DATA STATUS CHECK:")
        for sym in CONFIG["SYMBOLS"]:
            binance_ok = self.data_received[sym]["binance"]
            bybit_ok = self.data_received[sym]["bybit"]
            status = "✅" if binance_ok and bybit_ok else "❌"
            print(f"   {status} {sym}: Binance={binance_ok}, Bybit={bybit_ok}")

        print("\n" + "="*60)
        print("🚀 STARTING ARBITRAGE SCAN")
        print("="*60 + "\n")

        last_status_print = 0
        last_refresh = time.time()

        while self.running:
            now = time.time()

            # Print live spreads every 3 seconds (debug)
            if now - last_status_print > 3:
                print("\n🔍 LIVE SPREADS (Binance ask → Bybit bid):")
                active_count = 0
                for sym in CONFIG["SYMBOLS"]:
                    if self.is_fresh(sym, max_age=3):
                        active_count += 1
                        p = self.prices[sym]
                        spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
                        spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000
                        max_spread = max(spread1, spread2)
                        bar = "█" * int(min(50, max_spread * 2))
                        print(f"   {sym}: spread={max_spread:.2f}bps {bar} | Bin ask={p['binance_ask']:.8f} Byb bid={p['bybit_bid']:.8f}")
                    else:
                        print(f"   {sym}: ⏳ waiting for data...")
                print(f"   Active pairs: {active_count}/{len(CONFIG['SYMBOLS'])} | Balance: ${self.balance:.2f}")
                last_status_print = now

            # Close existing positions
            for sym in list(self.positions.keys()):
                self.check_and_close_arbitrage(sym)

            # Scan for new arbitrage opportunities
            for sym in CONFIG["SYMBOLS"]:
                if sym in self.positions:
                    continue
                if sym in self.last_trade_time and now - self.last_trade_time[sym] < CONFIG["COOLDOWN_SEC"]:
                    continue

                if not self.is_fresh(sym):
                    continue

                p = self.prices[sym]
                spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
                spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000

                required_spread = (CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"]) * 10000 + CONFIG["SLIPPAGE_BPS"] * 2 + CONFIG["TAKE_PROFIT_BPS"]

                if spread1 >= CONFIG["MIN_SPREAD_BPS"] and spread1 >= required_spread:
                    print(f"\n🔥 OPPORTUNITY FOUND! {sym} spread1={spread1:.2f}bps → BUY BINANCE / SELL BYBIT")
                    self.open_arbitrage(sym, "binance_buy_bybit_sell", "binance", "bybit", p["binance_ask"], p["bybit_bid"])
                elif spread2 >= CONFIG["MIN_SPREAD_BPS"] and spread2 >= required_spread:
                    print(f"\n🔥 OPPORTUNITY FOUND! {sym} spread2={spread2:.2f}bps → BUY BYBIT / SELL BINANCE")
                    self.open_arbitrage(sym, "bybit_buy_binance_sell", "bybit", "binance", p["bybit_ask"], p["binance_bid"])

            # Daily reset
            if now - self.daily_start >= 86400:
                print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                self.daily_profit = Decimal('0')
                self.daily_start = now

            await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(CrossArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
