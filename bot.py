import asyncio, json, os, websockets
from decimal import Decimal

# --- CONFIGURATION (إعدادات الربحية المتزنة) ---
SYMBOL = os.getenv("SYMBOL", "btcusdt")
TRADE_SIZE = Decimal("1000")
# رفعنا العتبة لفلترة الضوضاء (لن يدخل إلا في الفرص القوية)
ENTRY_THRESHOLD = Decimal("0.22") 
FEE_RATE = Decimal("0.00075") 
# رفعنا الهدف قليلاً لضمان تغطية الرسوم وزيادة الربح الصافي
TARGET_PROFIT = Decimal("0.0025") # 0.25%
STOP_LOSS = Decimal("-0.0040")

class GoldenSentinel:
    def __init__(self):
        self.balance = Decimal("5000.00")
        self.active_trade = None

    def get_weighted_imb(self, bids, asks):
        # نستخدم وزن أعمق (10 مستويات) لفلترة الحركات الوهمية
        b_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids[:10])
        a_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks[:10])
        return (b_w - a_w) / (b_w + a_w)

    async def start(self):
        # عدنا لـ depth20 لرؤية الصورة الكاملة للجدران
        url = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth20@100ms"
        print(f"🔱 Golden Sentinel Active | Aiming for Quality over Quantity")

        async with websockets.connect(url) as ws:
            while True:
                try:
                    data = json.loads(await ws.recv())
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    curr_imb = self.get_weighted_imb(bids, asks)

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        gross_roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        
                        total_fees = TRADE_SIZE * FEE_RATE * 2
                        net_pnl = (TRADE_SIZE * gross_roi) - total_fees
                        
                        # التعديل الجوهري: لا تخرج فوراً عند الـ Flip إلا إذا كان هناك خسارة حقيقية
                        # نعطي الصفقة فرصة طالما لم نضرب الـ Stop Loss
                        severe_flip = (t['side'] == "BUY" and curr_imb < -0.15) or (t['side'] == "SELL" and curr_imb > 0.15)

                        if (net_pnl / TRADE_SIZE) >= TARGET_PROFIT or (net_pnl / TRADE_SIZE) <= STOP_LOSS or severe_flip:
                            self.balance += net_pnl
                            status = "💰 PROFIT" if net_pnl > 0 else "🛡️ STOPPED"
                            print(f"{status} | Net: ${round(net_pnl, 2)} | Bal: ${round(self.balance, 2)}")
                            self.active_trade = None
                    else:
                        if curr_imb > ENTRY_THRESHOLD:
                            self.active_trade = {"side": "BUY", "entry": ask_p}
                            print(f"🦅 HUNTING LONG @ {ask_p} | Imb: {round(curr_imb, 2)}")
                        elif curr_imb < -ENTRY_THRESHOLD:
                            self.active_trade = {"side": "SELL", "entry": bid_p}
                            print(f"🦅 HUNTING SHORT @ {bid_p} | Imb: {round(curr_imb, 2)}")

                except Exception:
                    continue

if __name__ == "__main__":
    asyncio.run(GoldenSentinel().start())
