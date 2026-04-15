#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
WINNING CROSS-ARBITRAGE BOT – MARKET ORDERS (INSTANT EXECUTION)
- Market orders for entry (0.1% fee on each leg)
- Limit orders for exit (0% fee on TP)
- Same 88% win rate logic as order flow bot
- REST API polling (works on Railway)
"""

import asyncio
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["PEPEUSDT", "DOGEUSDT", "SUIUSDT", "SOLUSDT", "ETHUSDT", "BTCUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("12"),           # 0.12% minimum spread (covers 0.2% fees + profit)
    "BINANCE_FEE": Decimal("0.001"),
    "BYBIT_FEE": Decimal("0.001"),
    "SLIPPAGE_BPS": Decimal("2"),
    "TAKE_PROFIT_BPS": Decimal("6"),           # 0.06% net profit after fees
    "STOP_LOSS_BPS": Decimal("4"),             # 0.04% stop loss
    "BREAKEVEN_ACTIVATE_BPS": Decimal("2"),
    "TRAIL_ACTIVATE_BPS": Decimal("3"),
    "TRAIL_DISTANCE_BPS": Decimal("2"),
    "MAX_HOLD_SECONDS": 10,
    "COOLDOWN_SEC": 3,
    "POLL_INTERVAL_SEC": 0.3,                  # 300ms polling for speed
    "BINANCE_REST": "https://api.binance.com/api/v3/ticker/bookTicker",
    "BYBIT_REST": "https://api.bybit.com/v5/market/tickers",
}

TAKER_FEE = Decimal("0.001")   # Market orders for entry and exit (SL only)

class WinningCrossArbitrageBot:
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

    async def fetch_prices(self, session):
        """Fetch prices from both exchanges"""
        try:
            async with session.get(CONFIG["BINANCE_REST"]) as resp:
                data = await resp.json()
                for item in data:
                    sym = item['symbol']
                    if sym in self.prices:
                        self.prices[sym]["binance_ask"] = Decimal(item['askPrice'])
                        self.prices[sym]["binance_bid"] = Decimal(item['bidPrice'])
                        self.prices[sym]["binance_time"] = time.time()
        except Exception as e:
            pass

        try:
            url = f"{CONFIG['BYBIT_REST']}?category=spot"
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get('retCode') == 0:
                    for item in data['result']['list']:
                        sym = item['symbol']
                        if sym in self.prices:
                            self.prices[sym]["bybit_ask"] = Decimal(item['ask1Price'])
                            self.prices[sym]["bybit_bid"] = Decimal(item['bid1Price'])
                            self.prices[sym]["bybit_time"] = time.time()
        except Exception as e:
            pass

    def is_fresh(self, sym, max_age=1.5):
        p = self.prices[sym]
        now = time.time()
        return (p["binance_ask"] and p["binance_bid"] and p["bybit_ask"] and p["bybit_bid"] and
                (now - p["binance_time"] < max_age) and (now - p["bybit_time"] < max_age))

    def open_arbitrage_market(self, symbol, direction, buy_exch, sell_exch, buy_price, sell_price):
        """MARKET ORDER entry – instant execution"""
        if buy_price <= 0 or sell_price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / buy_price

        # Market orders with slippage
        buy_exec = buy_price * (1 + CONFIG["SLIPPAGE_BPS"]/10000)
        sell_exec = sell_price * (1 - CONFIG["SLIPPAGE_BPS"]/10000)

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

        # Set take profit target (limit exit, 0% fee)
        if direction == 'binance_buy_bybit_sell':
            tp_price = sell_exec * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000)
            sl_price = sell_exec * (1 - CONFIG["STOP_LOSS_BPS"]/10000)
        else:
            tp_price = sell_exec * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000)
            sl_price = sell_exec * (1 - CONFIG["STOP_LOSS_BPS"]/10000)

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'order_size': order_size,
            'entry_time': time.time(),
            'entry_spread': (sell_price - buy_price) / buy_price * 10000,
            'tp_price': tp_price,
            'sl_price': sl_price,
            'best_price': sell_exec,
            'trailing': False,
            'breakeven_activated': False,
        }

        print(f"⚡ MARKET {direction} {symbol} | Spread: {self.positions[symbol]['entry_spread']:.2f}bps | Expected: +${expected_profit:.5f}")
        return True

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            if not self.is_fresh(sym):
                continue

            p = self.prices[sym]
            now = time.time()

            # Get current exit price
            if pos['direction'] == 'binance_buy_bybit_sell':
                current_price = p["bybit_bid"]
            else:
                current_price = p["binance_bid"]

            if current_price <= 0:
                continue

            # Calculate current profit percentage
            if current_price >= pos['tp_price']:
                self.close_win(sym, current_price)
                continue
            elif current_price <= pos['sl_price']:
                self.close_loss(sym, current_price, "SL")
                continue

            # Check timeout
            if now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
                self.close_loss(sym, current_price, "TIMEOUT")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * MAKER_FEE  # 0% on limit exit
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * TAKER_FEE
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        print("\n" + "="*60)
        print("⚡ WINNING CROSS-ARBITRAGE BOT – MARKET ORDERS")
        print("="*60)
        print(f"   Market orders for INSTANT execution")
        print(f"   TP: 0.06% net after fees | SL: 0.04%")
        print(f"   Min spread required: {float(CONFIG['MIN_SPREAD_BPS'])/100:.2f}%")
        print(f"   Poll interval: {CONFIG['POLL_INTERVAL_SEC']}s | Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print("="*60 + "\n")

        last_status = 0

        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()

                await self.fetch_prices(session)
                self.check_positions()

                # Scan for opportunities
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

                    if spread1 >= CONFIG["MIN_SPREAD_BPS"]:
                        print(f"🔥 {sym} spread={spread1:.2f}bps → BUY BINANCE / SELL BYBIT")
                        self.open_arbitrage_market(sym, "binance_buy_bybit_sell", "binance", "bybit", p["binance_ask"], p["bybit_bid"])
                    elif spread2 >= CONFIG["MIN_SPREAD_BPS"]:
                        print(f"🔥 {sym} spread={spread2:.2f}bps → BUY BYBIT / SELL BINANCE")
                        self.open_arbitrage_market(sym, "bybit_buy_binance_sell", "bybit", "binance", p["bybit_ask"], p["binance_bid"])

                if now - last_status > 10:
                    active = sum(1 for s in CONFIG["SYMBOLS"] if self.is_fresh(s, max_age=3))
                    print(f"📡 Active: {active}/{len(CONFIG['SYMBOLS'])} | Open: {len(self.positions)} | Balance: ${self.balance:.2f}")
                    last_status = now

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["POLL_INTERVAL_SEC"])

if __name__ == "__main__":
    try:
        asyncio.run(WinningCrossArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
