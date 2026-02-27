# bot.py – hardened & verbose debug (with dedupe and active-trade guard)
import time
from datetime import datetime
import MetaTrader5 as mt5

from config import *
from mt5_utils import initialize_mt5, get_positions
from mt5_utils import place_order, get_symbol_info, has_active_trade_or_order, get_orders, cancel_order
from signals import calculate_indicators, get_signals, check_session
from risk_manager import RiskManager
from logger import tg, log_trade, log_error, get_trade_stats

class ScalpingBot:
    def __init__(self):
        self.risk_manager = RiskManager()
        self.is_running = False
        self.last_signal_time = {}
        self.start_time = datetime.now()

    def _d(self, msg: str):
        if DEBUG_MODE:
            print(f"[DEBUG {datetime.now().strftime('%H:%M:%S')}] {msg}")

    def initialize(self) -> bool:
        try:
            if not initialize_mt5():
                return False
            account = mt5.account_info()
            if not account or account.balance < MIN_EQUITY:
                tg(f"❌ Insufficient balance: ${getattr(account,'balance',0):.2f}")
                return False
            self.risk_manager.start_equity = account.equity
            self.risk_manager.peak_equity = account.equity
            tg(
                f"🚀 Scalping Bot Started\n"
                f"💰 Balance: ${account.balance:.2f}\n"
                f"📊 Symbols: {', '.join(SYMBOLS)}\n"
                f"⚡ Risk: {RISK_PERCENT*100:.2f}% per trade"
            )
            return True
        except Exception as e:
            log_error("INIT", e)
            return False

    def scan_symbol(self, symbol: str):
        try:
            in_session, boost = check_session(datetime.now())
            if not in_session:
                if DEBUG_MODE: self._d(f"{symbol} skipped (session off)")
                return None

            if symbol in self.last_signal_time and time.time() - self.last_signal_time[symbol] < SIGNAL_COOLDOWN:
                if DEBUG_MODE: self._d(f"{symbol} cooldown…")
                return None

            from mt5_utils import get_data
            df = get_data(symbol, count=200)
            if df.empty or len(df) < 50:
                if DEBUG_MODE: self._d(f"{symbol} not enough candles ({len(df)})")
                return None

            df = calculate_indicators(df)
            if df is None or df.empty:
                if DEBUG_MODE: self._d(f"{symbol} indicators empty")
                return None

            sig = get_signals(df, symbol)
            if not sig:
                if DEBUG_MODE: self._d(f"{symbol} → NO SIGNAL")
                return None

            sig["strength"] *= boost

            # ⛔ Guard: vienas aktyvus per symbol + direction
            if has_active_trade_or_order(symbol, sig["type"]):
                if DEBUG_MODE: self._d(f"{symbol} skip: already have {sig['type']} position/order")
                return None

            if not self.risk_manager.can_trade(symbol, sig["strength"]):
                if DEBUG_MODE: self._d(f"{symbol} blocked by can_trade (daily cap/risk)")
                return None

            self.last_signal_time[symbol] = time.time()
            if DEBUG_MODE:
                self._d(f"{symbol} → SIGNAL {sig['type'].upper()} "
                        f"(S:{sig['strength']:.2f}, ATR:{sig['atr']:.6f}) | "
                        f"reasons: {', '.join(sig.get('reasons', [])[:2])}")
            sig["symbol"] = symbol
            sig["df"] = df
            return sig
        except Exception as e:
            log_error(f"SCAN_{symbol}", e)
            return None

    def execute_signal(self, signal: dict) -> bool:
        try:
            symbol = signal["symbol"]
            signal_type = signal["type"]
            strength = signal["strength"]
            df = signal["df"]

            account = mt5.account_info()
            if not account:
                return False

            atr = max(df.iloc[-1]["atr"], 1e-12)
            lot = self.risk_manager.calculate_lot_size(symbol, strength, atr, account.equity)
            if lot <= 0:
                if DEBUG_MODE: self._d(f"{symbol} lot <= 0 (calc blocked)")
                return False

            sl, tp = self.risk_manager.calculate_sl_tp(df, symbol, signal_type, atr)
            if sl is None or tp is None:
                if DEBUG_MODE: self._d(f"{symbol} SL/TP calc failed")
                return False

            result = place_order(symbol, signal_type, lot, sl, tp, comment=f"S:{strength:.2f}", atr=atr)
            if not result:
                if DEBUG_MODE: self._d(f"{symbol} order_send failed")
                return False

            self.risk_manager.on_trade_open(symbol)

            log_trade("OPEN", symbol, signal_type, result.price, lot, 0, (signal.get("reasons") or [""])[0])
            sym = get_symbol_info(symbol)
            risk_pips = abs(result.price - sl) / sym.point
            reward_pips = abs(tp - result.price) / sym.point
            rr = reward_pips / risk_pips if risk_pips else 0
            tg(
                f"✅ TRADE: {symbol} {signal_type.upper()}\n"
                f"📍 Entry: {result.price:.5f}\n"
                f"🛑 SL: {sl:.5f} (-{risk_pips:.1f} pips)\n"
                f"🎯 TP: {tp:.5f} (+{reward_pips:.1f} pips)\n"
                f"💪 Strength: {strength:.2f} | RR: {rr:.1f}\n"
                f"📝 {(signal.get('reasons') or [''])[0]}"
            )
            return True
        except Exception as e:
            log_error(f"EXECUTE_{signal.get('symbol','?')}", e)
            return False

    def manage_positions(self):
        try:
            positions = get_positions()
            for pos in positions:
                from mt5_utils import get_data, close_position, modify_sl
                df = get_data(pos.symbol, count=60)
                if df.empty or len(df) < 20:
                    continue
                df = calculate_indicators(df)

                # Trailing to BE+ based on ATR
                if pos.profit > 0:
                    sym = mt5.symbol_info(pos.symbol)
                    atr = max(df.iloc[-1]["atr"], 1e-12)
                    offset = max(atr * 0.5, 50 * sym.point)
                    new_sl = pos.price_open + offset if pos.type == mt5.POSITION_TYPE_BUY else pos.price_open - offset
                    new_sl = round(new_sl, sym.digits)
                    if abs((pos.sl or 0) - new_sl) > 1e-6:
                        modify_sl(pos.ticket, new_sl)

                # Simple time-stop on losers
                age_min = (datetime.now() - pos.time).total_seconds() / 60 if isinstance(pos.time, datetime) else 0
                if age_min > 240 and pos.profit < 0:
                    if close_position(pos.ticket):
                        log_trade("CLOSE", pos.symbol, "timeout", 0, pos.volume, pos.profit, "Time stop")
                        self.risk_manager.update_performance(pos.symbol, pos.profit)
                        tg(f"⏰ Time stop: {pos.symbol} #{pos.ticket} ${pos.profit:.2f}")
        except Exception as e:
            log_error("MANAGE_POSITIONS", e)

    def dedupe_pending_orders(self):
        """
        Palieka po 1 pending orderį per symbol+direction; kitus pašalina.
        """
        try:
            orders = get_orders()
            if not orders:
                return
            buckets = {}
            for o in orders:
                side = "buy" if o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP) else "sell"
                buckets.setdefault((o.symbol, side), []).append(o)
            for (sym, side), lst in buckets.items():
                if len(lst) <= 1:
                    continue
                tick = mt5.symbol_info_tick(sym)
                if not tick:
                    continue
                ref = tick.ask if side == "buy" else tick.bid
                lst_sorted = sorted(lst, key=lambda o: abs(o.price_open - ref))
                keep = lst_sorted[0].ticket
                for o in lst_sorted[1:]:
                    cancel_order(o.ticket)
                    if DEBUG_MODE: self._d(f"{sym} dedupe removed pending #{o.ticket}, kept #{keep}")
        except Exception as e:
            log_error("DEDUPE_PENDING", e)

    def print_stats(self):
        try:
            account = mt5.account_info()
            if not account:
                return
            stats = get_trade_stats()
            runtime = (datetime.now() - self.start_time).total_seconds() / 3600
            total_return = (
                (account.equity - self.risk_manager.start_equity) / self.risk_manager.start_equity * 100
            ) if self.risk_manager.start_equity else 0.0
            msg = (
                f"📊 PERFORMANCE UPDATE\n"
                f"{'='*25}\n"
                f"⏱️ Running: {runtime:.1f}h\n"
                f"💰 Equity: ${account.equity:.2f}\n"
                f"📈 Return: {total_return:.2f}%\n"
                f"🎯 Trades today: {self.risk_manager.daily_trades}/{MAX_TRADES_PER_DAY}\n"
                f"✅ Win Rate: {stats.get('win_rate', 0):.1f}%\n"
                f"💸 Total P&L: ${stats.get('total_profit', 0):.2f}"
            )
            print(msg)
            tg(msg)
        except Exception as e:
            log_error("STATS", e)

    def run(self):
        self.is_running = True
        if not self.initialize():
            return
        last_stats_time = time.time()
        scan_cnt = 0
        try:
            while self.is_running:
                try:
                    if not mt5.account_info():
                        tg("⚠️ Connection lost, reconnecting...")
                        time.sleep(30)
                        if not initialize_mt5():
                            continue

                    if datetime.now().hour == 0 and datetime.now().minute == 0:
                        self.risk_manager.reset_daily()

                    for sym in SYMBOLS:
                        sig = self.scan_symbol(sym)
                        if sig:
                            self.execute_signal(sig)
                            time.sleep(2)

                    self.manage_positions()
                    self.dedupe_pending_orders()

                    if time.time() - last_stats_time > 1800:
                        self.print_stats()
                        last_stats_time = time.time()

                    scan_cnt += 1
                    if scan_cnt % 10 == 0 and DEBUG_MODE:
                        self._d(f"Scan heartbeat #{scan_cnt}")

                    sleep_time = 10 if get_positions() else 20
                    time.sleep(sleep_time)

                except Exception as e:
                    log_error("MAIN_LOOP", e)
                    time.sleep(60)
        except KeyboardInterrupt:
            tg("🛑 Bot stopped by user")
        finally:
            self.shutdown()

    def shutdown(self):
        self.is_running = False
        self.print_stats()
        mt5.shutdown()
        tg("✅ Bot shutdown complete")

def main():
    ScalpingBot().run()

if __name__ == "__main__":
    main()
