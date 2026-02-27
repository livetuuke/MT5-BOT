import MetaTrader5 as mt5
from collections import defaultdict, deque
from config import *

class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.peak_equity = 0.0
        self.start_equity = 0.0
        self.symbol_performance = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        self.symbol_history = defaultdict(lambda: deque(maxlen=20))

    # ----- limits / guards -----
    def can_trade(self, symbol: str, strength: float) -> bool:
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return False
        if self.consecutive_losses >= 5:
            return False
        return True

    def on_trade_open(self, symbol: str):
        self.daily_trades += 1
        self.symbol_performance[symbol]["trades"] += 1

    def update_performance(self, symbol: str, profit: float):
        self.daily_pnl += profit
        self.symbol_performance[symbol]["pnl"] += profit
        if profit > 0:
            self.symbol_performance[symbol]["wins"] += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0

    # ----- lot sizing -----
    def _symbol_risk_multiplier(self, symbol: str) -> float:
        return SYMBOL_CONFIG.get(symbol, {}).get("risk_multiplier", 1.0)

    def _pip_value_per_lot(self, sym) -> float:
        if sym is None:
            return 10.0
        ttv = sym.trade_tick_value or 0.0
        tts = sym.trade_tick_size or sym.point or 0.0001
        if tts == 0:
            return 10.0
        return ttv / tts

    def calculate_lot_size(self, symbol: str, signal_strength: float, atr: float, equity: float) -> float:
        try:
            sym = mt5.symbol_info(symbol)
            if not sym or atr is None or atr <= 0:
                return 0.01

            dyn = max(0.7, min(1.3, signal_strength / 0.8))
            base_risk = RISK_PERCENT * self._symbol_risk_multiplier(symbol) * dyn
            risk_amount = max(0.0, equity * base_risk)

            sl_dist_price = atr * ATR_SL_MULTIPLIER
            value_per_price = self._pip_value_per_lot(sym)
            loss_per_lot = sl_dist_price * value_per_price
            if loss_per_lot <= 0:
                return 0.01

            lot = risk_amount / loss_per_lot

            lot = max(sym.volume_min, min(lot, sym.volume_max))
            steps = round((lot - sym.volume_min) / sym.volume_step)
            lot = sym.volume_min + steps * sym.volume_step

            # --- Margin safeguard ---
            try:
                tick = mt5.symbol_info_tick(sym.name)
                price_for_margin = (tick.ask if signal_strength >= 0 else tick.bid) if tick else 0
                order_type = mt5.ORDER_TYPE_BUY if signal_strength >= 0 else mt5.ORDER_TYPE_SELL
                for _ in range(6):
                    req_margin = mt5.order_calc_margin(order_type, sym.name, float(lot), float(price_for_margin))
                    ai = mt5.account_info()
                    if ai and req_margin and req_margin > 0:
                        if req_margin <= (ai.free_margin * 0.90):
                            break
                        # sumažinam ~40% ir normalizuojam iki step
                        lot = max(sym.volume_min, lot * 0.6)
                        steps = round((lot - sym.volume_min) / sym.volume_step)
                        lot = sym.volume_min + steps * sym.volume_step
                    else:
                        break
            except Exception:
                pass

            return round(lot, 2)
        except Exception as e:
            print(f"❌ Lot size error: {e}")
            return 0.01

    def calculate_sl_tp(self, df, symbol: str, signal_type: str, atr: float):
        try:
            last = df.iloc[-1]
            entry = last["close"]
            sl_dist = atr * ATR_SL_MULTIPLIER
            tp_dist = atr * ATR_TP_MULTIPLIER
            if signal_type == "buy":
                sl = entry - sl_dist
                tp = entry + tp_dist
            else:
                sl = entry + sl_dist
                tp = entry - tp_dist

            sym = mt5.symbol_info(symbol)
            digits = sym.digits if sym else 5
            return round(sl, digits), round(tp, digits)
        except Exception:
            return None, None
