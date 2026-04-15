#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-EXCHANGE OFI ARBITRAGE – BINANCE FUTURES → BYBIT LINEAR
- Monitors Binance Futures order book for OFI
- Compares prices between both exchanges
- Only trades when price difference > fees (0.105%)
- Front-runs Bybit when Binance shows strong OFI
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time
from collections import deque

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "DEPTH_LEVELS": 10,
    "OFI_THRESHOLD": Decimal("0.70"),           # Binance OFI threshold
    "MIN_SPREAD_BPS": Decimal("12"),            # 0.12% minimum price difference
    "BINANCE_FEE": Decimal("0.0005"),           # 0.05% taker
    "BYBIT_FEE": Decimal("0.00055"),            # 0.055% taker
    "TOTAL_FEES_BPS": Decimal("10.5"),          # 0.105% combined
    "TAKE_PROFIT_BPS": Decimal("8"),            # 0.08% net profit
    "STOP_LOSS_BPS": Decimal("10"),             # 0.10% stop loss
    "MAX_HOLD_SECONDS": 10,
    "COOLDOWN_SEC": 5,
    "MAX_LATENCY_MS": 200,
    "SCAN_INTERVAL_MS": 50,
    "BINANCE_FUTURES_WS": "wss://fstream.binance.com/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class CrossExchangeOFIBot:
    def __init__(self):
        self.binance_books = {}
        self.bybit_books = {}
        self.bybit_prices = {}
        self.binance_prices = {}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.last_binance_time = {}
        self.last_bybit_time = {}
        self.running = True

    class OrderBook:
        def __init__(self, symbol):
            self.symbol = symbol
            self.bids = {}
            self.asks = {}
            self.last_update = 0.0

        def apply_snapshot(self, bids, asks):
            self.bids = {Decimal(p): Decimal(q) for p, q in bids[:20]}
            self.asks = {Decimal(p): Decimal(q) for p, q in asks[:20]}
            self.last_update = time.time()

        def apply_delta(self, bids, asks):
            for price, qty in bids:
                p, q = Decimal(price), Decimal(qty)
                if q == 0:
                    self.bids.pop(p, None)
                else:
                    self.bids[p] = q
            for price, qty in asks:
                p, q = Decimal(price), Decimal(qty)
                if q == 0:
                    self.asks.pop(p, None)
                else:
                    self.asks[p] = q
            self.last_update = time.time()

        def best_bid(self):
            return max(self.bids.keys()) if self.bids else Decimal('0')

        def best_ask(self):
            return min(self.asks.keys()) if self.asks else Decimal('0')

        def mid_price(self):
            bb, ba = self.best_bid(), self.best_ask()
            return (bb + ba) / 2 if bb and ba else Decimal('0')

        def get_ofi(self, depth=10):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_vol = sum(q * (depth - i) for i, (_, q) in enumerate(sorted_bids))
            ask_vol = sum(q * (depth - i) for i, (_, q) in enumerate(sorted_asks))
            total = bid_vol + ask_vol
            if total == 0:
                return Decimal('0')
            return (bid_vol - ask_vol) / total

    async def subscribe_binance_futures(self, symbol):
        """Binance Futures WebSocket"""
        stream = f"{symbol.lower()}@depth20@100ms"
        url = f"{CONFIG['BINANCE_FUTURES_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"✅ Binance Futures {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if symbol not in self.binance_books:
                            self.binance_books[symbol] = self.OrderBook(symbol)
                        # Binance sends snapshots and deltas
                        if 'bids' in data and 'asks' in data:
                            self.binance_books[symbol].apply_snapshot(data['bids'], data['asks'])
                        else:
                            self.binance_books[symbol].apply_delta(data.get('b', []), data.get('a', []))
                        self.binance_prices[symbol] = self.binance_books[symbol].mid_price()
                        self.last_binance_time[symbol] = time.time()
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
                    print(f"✅ Bybit Linear {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'topic' in data and 'data' in data:
                            if symbol not in self.bybit_books:
                                self.bybit_books[symbol] = self.OrderBook(symbol)
                            book_data = data['data']
                            if 'b' in book_data and 'a' in book_data:
                                self.bybit_books[symbol].apply_snapshot(book_data['b'], book_data['a'])
                                self.bybit_prices[symbol] = self.bybit_books[symbol].mid_price()
                                self.last_bybit_time[symbol] = time.time()
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    def is_fresh(self, symbol, max_age=1.0):
        """Check if both exchanges have recent data"""
        binance_ok = symbol in self.last_binance_time and (time.time() - self.last_binance_time[symbol]) < max_age
        bybit_ok = symbol in self.last_bybit_time and (time.time() - self.last_bybit_time[symbol]) < max_age
        return binance_ok and bybit_ok

    def check_price_difference(self, symbol):
        """Calculate price difference between Binance and Bybit"""
        if symbol not in self.binance_prices or symbol not in self.bybit_prices:
            return None, None
        binance_price = self.binance_prices[symbol]
        bybit_price = self.bybit_prices[symbol]
        if binance_price <= 0 or bybit_price <= 0:
            return None, None
        # Spread in basis points
        spread_bps = abs(binance_price - bybit_price) / min(binance_price, bybit_price) * 10000
        return spread_bps, binance_price > bybit_price

    def open_arbitrage(self, symbol, direction, buy_exch, sell_exch, buy_price, sell_price):
        """Execute arbitrage trade"""
        if buy_price <= 0 or sell_price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / buy_price
        entry_fee = order_size * CONFIG["BINANCE_FEE"]  # Approximate

        if order_size + entry_fee > self.balance:
            return False

        self.balance -= (order_size + entry_fee)

        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]

        # For long: buy cheap, sell expensive
        if direction == 'binance_cheaper':
            target_price = sell_price * (1 + tp_bps/10000)
            stop_price = sell_price * (1 - sl_bps/10000)
        else:
            target_price = sell_price * (1 + tp_bps/10000)
            stop_price = sell_price * (1 - sl_bps/10000)

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'order_size': order_size,
            'entry_time': time.time(),
            'target_price': target_price,
            'stop_price': stop_price,
        }

        net_profit_target = order_size * tp_bps/10000
        print(f"🔄 ARBITRAGE OPEN {symbol} | {direction} | Spread: {CONFIG['MIN_SPREAD_BPS']:.1f}bps | Target: +${net_profit_target:.5f}")
        return True

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            if sym not in self.bybit_prices:
                continue
            current_price = self.bybit_prices[sym]
            if current_price <= 0:
                continue

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
        fee = gross * CONFIG["BYBIT_FEE"]
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * CONFIG["BYBIT_FEE"]
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | ${profit:.5f} ({profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        # Start WebSocket connections
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_binance_futures(sym))
            asyncio.create_task(self.subscribe_bybit_linear(sym))

        print("\n" + "="*60)
        print("🚀 CROSS-EXCHANGE OFI ARBITRAGE – BINANCE → BYBIT")
        print("="*60)
        print(f"   Monitoring {len(CONFIG['SYMBOLS'])} pairs")
        print(f"   OFI threshold: {CONFIG['OFI_THRESHOLD']} | Min spread: {CONFIG['MIN_SPREAD_BPS']}bps")
        print(f"   Total fees: {CONFIG['TOTAL_FEES_BPS']}bps | TP: {CONFIG['TAKE_PROFIT_BPS']}bps net")
        print("="*60 + "\n")

        last_status = 0
        last_ofi_print = 0

        while self.running:
            now = time.time()

            # Print OFI and price differences every 5 seconds
            if now - last_ofi_print > 5:
                print("\n🔍 MARKET STATUS:")
                for sym in CONFIG["SYMBOLS"]:
                    if self.is_fresh(sym):
                        binance_ofi = self.binance_books[sym].get_ofi(CONFIG["DEPTH_LEVELS"]) if sym in self.binance_books else Decimal('0')
                        spread, binance_higher = self.check_price_difference(sym)
                        binance_price = self.binance_prices.get(sym, 0)
                        bybit_price = self.bybit_prices.get(sym, 0)
                        print(f"   {sym}: Binance OFI={binance_ofi:.2f} | Spread={spread:.1f}bps | Bin={binance_price:.6f} Byb={bybit_price:.6f}")
                last_ofi_print = now

            self.check_positions()

            # Scan for arbitrage opportunities
            for sym in CONFIG["SYMBOLS"]:
                if sym in self.positions:
                    continue
                if sym in self.last_trade_time and now - self.last_trade_time[sym] < CONFIG["COOLDOWN_SEC"]:
                    continue

                if not self.is_fresh(sym):
                    continue

                # Get Binance OFI
                binance_ofi = self.binance_books[sym].get_ofi(CONFIG["DEPTH_LEVELS"]) if sym in self.binance_books else Decimal('0')
                
                # Check price difference
                spread, binance_higher = self.check_price_difference(sym)
                
                if spread is None or spread < CONFIG["MIN_SPREAD_BPS"]:
                    continue

                # STRATEGY: Binance OFI > threshold AND price difference > fees
                if binance_ofi > CONFIG["OFI_THRESHOLD"]:
                    if binance_higher:
                        # Binance price is HIGHER than Bybit → buy Bybit (cheaper)
                        print(f"🔥 OPPORTUNITY {sym}: Binance OFI={binance_ofi:.2f} | Binance price is HIGHER | Spread={spread:.1f}bps → BUY BYBIT, SELL BINANCE")
                        self.open_arbitrage(sym, 'bybit_cheaper', 'bybit', 'binance', 
                                           self.bybit_prices[sym], self.binance_prices[sym])
                    else:
                        # Binance price is LOWER than Bybit → buy Binance (cheaper)
                        print(f"🔥 OPPORTUNITY {sym}: Binance OFI={binance_ofi:.2f} | Binance price is LOWER | Spread={spread:.1f}bps → BUY BINANCE, SELL BYBIT")
                        self.open_arbitrage(sym, 'binance_cheaper', 'binance', 'bybit',
                                           self.binance_prices[sym], self.bybit_prices[sym])

            # Daily reset
            if now - self.daily_start >= 86400:
                print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                self.daily_profit = Decimal('0')
                self.daily_start = now

            await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(CrossExchangeOFIBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
