#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-EXCHANGE ARBITRAGE BOT – WITH AUTO TRADING
- Detects price jumps on Binance
- Executes arbitrage when spread > fees (10.5bps)
- Simulated trades (ready for live API)
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
    "MIN_ARBITRAGE_BPS": Decimal("12"),          # 0.12% minimum spread (covers 10.5bps fees + buffer)
    "TAKE_PROFIT_BPS": Decimal("5"),             # 0.05% net profit target
    "STOP_LOSS_BPS": Decimal("8"),               # 0.08% stop loss
    "MAX_HOLD_SECONDS": 5,
    "COOLDOWN_SEC": 10,
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class AutoArbitrageBot:
    def __init__(self):
        self.binance_prices = {}
        self.bybit_prices = {}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.last_trade_time = {}
        self.last_binance_time = {}
        self.last_bybit_time = {}
        self.running = True
        self.last_price_print = 0

    async def subscribe_binance_spot(self, symbol):
        """Binance Spot – bookTicker stream"""
        stream = f"{symbol}@bookTicker"
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        ask_price = Decimal(data['a'])
                        bid_price = Decimal(data['b'])
                        mid_price = (ask_price + bid_price) / 2
                        
                        old_price = self.binance_prices.get(symbol, mid_price)
                        self.binance_prices[symbol] = mid_price
                        self.binance_asks = ask_price
                        self.binance_bids = bid_price
                        self.last_binance_time[symbol] = time.time()
                        
                        # Detect price jump
                        if old_price > 0:
                            change_bps = abs(mid_price - old_price) / old_price * 10000
                            if change_bps >= CONFIG["PRICE_JUMP_BPS"]:
                                self.check_arbitrage_opportunity(symbol, mid_price)
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    async def subscribe_bybit_linear(self, symbol):
        """Bybit Linear – orderbook.50 stream"""
        while self.running:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.50.{symbol.upper()}"]}
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

    def check_arbitrage_opportunity(self, symbol, binance_price):
        """Check if arbitrage opportunity exists and execute"""
        if symbol not in self.bybit_prices:
            return
        if symbol in self.positions:
            return
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return

        bybit_price = self.bybit_prices[symbol]
        if bybit_price <= 0:
            return

        # Calculate spread
        spread_bps = abs(binance_price - bybit_price) / min(binance_price, bybit_price) * 10000
        
        # Check if spread > minimum required
        if spread_bps < CONFIG["MIN_ARBITRAGE_BPS"]:
            return

        # Determine direction
        if binance_price > bybit_price:
            # Binance higher → Buy Bybit, Sell Binance
            print(f"\n🎯 ARBITRAGE OPPORTUNITY on {symbol.upper()}!")
            print(f"   Spread: {spread_bps:.2f}bps > {CONFIG['MIN_ARBITRAGE_BPS']}bps")
            print(f"   Buy Bybit @ {bybit_price:.8f}")
            print(f"   Sell Binance @ {binance_price:.8f}")
            print(f"   Expected profit: ${CONFIG['ORDER_SIZE_USDT'] * (spread_bps - 10.5)/10000:.5f}")
            self.execute_arbitrage(symbol, 'buy_bybit_sell_binance', bybit_price, binance_price)
        else:
            # Bybit higher → Buy Binance, Sell Bybit
            print(f"\n🎯 ARBITRAGE OPPORTUNITY on {symbol.upper()}!")
            print(f"   Spread: {spread_bps:.2f}bps > {CONFIG['MIN_ARBITRAGE_BPS']}bps")
            print(f"   Buy Binance @ {binance_price:.8f}")
            print(f"   Sell Bybit @ {bybit_price:.8f}")
            print(f"   Expected profit: ${CONFIG['ORDER_SIZE_USDT'] * (spread_bps - 10.5)/10000:.5f}")
            self.execute_arbitrage(symbol, 'buy_binance_sell_bybit', binance_price, bybit_price)

    def execute_arbitrage(self, symbol, direction, buy_price, sell_price):
        """Execute arbitrage trade (simulated)"""
        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                print(f"⚠️ Insufficient balance for {symbol}")
                return

        qty = order_size / buy_price
        entry_fee = order_size * Decimal("0.00055")  # Bybit fee approx
        total_cost = order_size + entry_fee

        if total_cost > self.balance:
            return

        self.balance -= total_cost

        # Set take profit and stop loss
        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]
        
        if direction == 'buy_bybit_sell_binance':
            target_price = sell_price * (1 - tp_bps/10000)
            stop_price = sell_price * (1 - sl_bps/10000)
        else:
            target_price = sell_price * (1 - tp_bps/10000)
            stop_price = sell_price * (1 - sl_bps/10000)

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'order_size': order_size,
            'entry_time': time.time(),
            'buy_price': buy_price,
            'sell_price': sell_price,
            'target_price': target_price,
            'stop_price': stop_price,
        }

        spread_bps = abs(sell_price - buy_price) / buy_price * 10000
        expected_profit = order_size * (spread_bps - 10.5) / 10000
        print(f"✅ ARBITRAGE EXECUTED {symbol.upper()} | Expected profit: +${expected_profit:.5f}")
        self.last_trade_time[symbol] = time.time()

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            if sym not in self.binance_prices:
                continue
            current_price = self.binance_prices[sym]
            now = time.time()

            if current_price >= pos['target_price']:
                self.close_win(sym, current_price)
            elif current_price <= pos['stop_price']:
                self.close_loss(sym, current_price, "SL")
            elif now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
                self.close_loss(sym, current_price, "TIMEOUT")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * Decimal("0.0005")  # Binance fee approx
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"✅ WIN {sym.upper()} | Profit: ${profit:.5f} | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * Decimal("0.0005")
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"❌ {reason} {sym.upper()} | Profit: ${profit:.5f} | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")

    async def run(self):
        # Start WebSocket connections
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_binance_spot(sym))
            asyncio.create_task(self.subscribe_bybit_linear(sym))

        print("\n" + "="*60)
        print("🚀 AUTO ARBITRAGE BOT – EXECUTING TRADES")
        print("="*60)
        print(f"   Min arbitrage spread: {CONFIG['MIN_ARBITRAGE_BPS']}bps")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print("="*60 + "\n")

        while self.running:
            now = time.time()
            
            # Print status every 10 seconds
            if now - self.last_price_print > 10:
                print(f"\n📊 STATUS | Balance: ${self.balance:.2f} | Trades: {self.total_trades} | WR: {(self.winning_trades/self.total_trades*100) if self.total_trades else 0:.1f}%")
                self.last_price_print = now

            self.check_positions()
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try:
        asyncio.run(AutoArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
