# mt5_utils.py — robust MT5 helpers + trading readiness checks + dedupe utilities

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
import time as _time
from config import *

# -----------------------------
# Timeframe map
# -----------------------------
TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
}

_COMMON_SUFFIXES = [".r", ".a", ".pro", ".m", ".i", ".ecn", ".raw"]

# -----------------------------
# Logging helper
# -----------------------------
def _log(symbol: str, msg: str) -> None:
    if DEBUG_MODE:
        print(f"[{symbol}] {msg}")

# -----------------------------
# Human-readable readiness report
# -----------------------------
def trading_readiness_report(print_ok: bool = False) -> dict:
    """
    Patikrina terminalą/sąskaitą. Spausdina tik kai yra problema
    (nebent print_ok=True arba DEBUG_MODE=True).
    """
    report = {}
    try:
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        report["terminal_connected"] = bool(ti)
        report["account_connected"] = bool(ai)
        report["terminal_trade_allowed"] = bool(getattr(ti, "trade_allowed", False))
        report["tradeapi_disabled"] = bool(getattr(ti, "tradeapi_disabled", False))
        report["account_trade_allowed"] = bool(getattr(ai, "trade_allowed", False)) if ai else False
        report["server"] = getattr(ai, "server", None) if ai else None
        report["login"] = getattr(ai, "login", None) if ai else None
    except Exception:
        pass

    problems = []
    if not report.get("terminal_connected"): problems.append("Terminal not connected")
    if not report.get("account_connected"): problems.append("Account not connected")
    if not report.get("terminal_trade_allowed"): problems.append("Algo Trading OFF")
    if report.get("tradeapi_disabled"): problems.append("tradeapi_disabled=True")
    if not report.get("account_trade_allowed"): problems.append("Account trading not allowed")

    should_print = bool(problems) or print_ok or DEBUG_MODE
    if should_print:
        print("──────────────── MT5 Trading Readiness ────────────────")
        print(f"Terminal connected: {report.get('terminal_connected')}")
        print(f"Account connected : {report.get('account_connected')}")
        print(f"Terminal trade allowed (Algo Trading button): {report.get('terminal_trade_allowed')}")
        print(f"Terminal tradeapi_disabled flag: {report.get('tradeapi_disabled')}")
        print(f"Account trade allowed (not investor pwd): {report.get('account_trade_allowed')}")
        if report.get("server"):
            print(f"Server: {report.get('server')} | Login: {report.get('login')}")
        if problems:
            print("Hints:")
            print("  1) Įjunk 'Algo Trading' (viršuje mygtukas – žalias).")
            print("  2) Tools → Options → Expert Advisors → 'Allow algo trading'.")
            print("  3) Neprisijunk investor (read-only) slaptažodžiu.")
        print("───────────────────────────────────────────────────────")
    return report

# -----------------------------
# MT5 bootstrap
# -----------------------------
def initialize_mt5() -> bool:
    try:
        if not mt5.initialize():
            print(f"❌ MT5 initialize failed: {mt5.last_error()}")
            return False
        acc = mt5.account_info()
        if acc:
            print(f"✅ MT5 Connected: {acc.login} | Balance: ${acc.balance:.2f}")
        trading_readiness_report(print_ok=False)
        return True
    except Exception as e:
        print(f"❌ MT5 init exception: {e}")
        return False

# -----------------------------
# Symbol resolver (handles Pepperstone suffixes)
# -----------------------------
def resolve_symbol(base: str) -> str | None:
    """Find real tradable symbol (e.g., EURUSD.r)."""
    si = mt5.symbol_info(base)
    if si:
        return si.name
    for suf in _COMMON_SUFFIXES:
        name = f"{base}{suf}"
        si = mt5.symbol_info(name)
        if si:
            return si.name
    cands = mt5.symbols_get(f"{base}*")
    if cands:
        # prefer tradeable
        for c in cands:
            if getattr(c, "trade_mode", 0) > 0:
                return c.name
        return cands[0].name
    return None

# -----------------------------
# Market open guard
# -----------------------------
def _is_market_open(si) -> bool:
    """
    Rinka laikoma uždaryta, jei:
    - trade_mode ne FULL, arba
    - paskutinis tick senesnis nei 5 min, arba
    - bid/ask = 0
    """
    if not si:
        return False
    try:
        if getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 2) != si.trade_mode:
            return False
        tick = mt5.symbol_info_tick(si.name)
        if not tick or (tick.bid == 0 or tick.ask == 0):
            return False
        if hasattr(tick, "time") and (_time.time() - tick.time) > 300:
            return False
        return True
    except Exception:
        return False

# -----------------------------
# History helpers
# -----------------------------
def _normalize_rates(df: pd.DataFrame) -> pd.DataFrame:
    if "tick_volume" not in df.columns and "real_volume" in df.columns:
        df.rename(columns={"real_volume": "tick_volume"}, inplace=True)
    needed = {"time", "open", "high", "low", "close", "tick_volume"}
    if not needed.issubset(set(df.columns)):
        return pd.DataFrame()
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]].copy()

def get_data(symbol: str, timeframe: str = "M1", count: int = 1000) -> pd.DataFrame:
    try:
        tf = TF_MAP.get(timeframe.upper(), mt5.TIMEFRAME_M1)
        real_symbol = resolve_symbol(symbol) or symbol
        si = mt5.symbol_info(real_symbol)
        if not si:
            _log(symbol, "resolve failed → symbol not in terminal")
            return pd.DataFrame()
        if not si.visible:
            mt5.symbol_select(real_symbol, True)

        rates = mt5.copy_rates_from_pos(real_symbol, tf, 0, int(count))
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df = _normalize_rates(df)
            if not df.empty:
                _log(symbol, f"history ok (from_pos): {len(df)} bars")
                return df

        end = datetime.now()
        start = end - timedelta(days=7)
        rates = mt5.copy_rates_range(real_symbol, tf, start, end)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df = _normalize_rates(df)
            if not df.empty:
                _log(symbol, f"history ok (7d range): {len(df)} bars")
                return df

        start = end - timedelta(days=30)
        rates = mt5.copy_rates_range(real_symbol, tf, start, end)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df = _normalize_rates(df)
            if not df.empty:
                _log(symbol, f"history ok (30d range): {len(df)} bars")
                return df

        _log(symbol, "no history (0 bars). Add to Market Watch and open a chart once.")
        return pd.DataFrame()
    except Exception as e:
        _log(symbol, f"get_data error: {e}")
        return pd.DataFrame()

def get_symbol_info(symbol: str):
    name = resolve_symbol(symbol) or symbol
    return mt5.symbol_info(name)

# -----------------------------
# Broker filling modes (prefer IOC)
# -----------------------------
def _broker_modes(symbol: str) -> list[int]:
    name = resolve_symbol(symbol) or symbol
    sym = mt5.symbol_info(name)
    preferred = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]
    modes = []
    if sym and sym.filling_mode in preferred:
        modes.append(sym.filling_mode)
    for m in preferred:
        if m not in modes:
            modes.append(m)
    return modes

def _normalize_price(symbol: str, price: float) -> float:
    sym = get_symbol_info(symbol)
    digits = sym.digits if sym else 5
    return round(price, digits)

def _spread_ok(symbol: str) -> bool:
    try:
        si = get_symbol_info(symbol)
        if not si:
            return True
        tk = mt5.symbol_info_tick(si.name)
        if not tk:
            return True
        spread_points = (tk.ask - tk.bid) / si.point
        pips = spread_points / (10 if si.digits in (3,5) else 1)
        max_pips = SYMBOL_CONFIG.get(symbol, {}).get("spread_filter", 2.0)
        return pips <= max_pips
    except Exception:
        return True

# -----------------------------
# Stops validator
# -----------------------------
def _ensure_valid_stops(si, order_type, entry_price, sl, tp):
    """
    Užtikrina, kad SL/TP teisingoje pusėje ir ne per arti (>= stops_level).
    Grąžina (sl, tp).
    """
    if sl is None and tp is None:
        return sl, tp

    min_dist = max(getattr(si, "stops_level", 0), 0) * si.point
    if min_dist == 0:
        min_dist = 3 * si.point  # konservatyvus buferis

    def roundp(x): 
        return _normalize_price(si.name, x) if x is not None else None

    if order_type == mt5.ORDER_TYPE_BUY:
        if sl is not None:
            sl = min(sl, entry_price - min_dist)
        if tp is not None:
            tp = max(tp, entry_price + min_dist)
    else:
        if sl is not None:
            sl = max(sl, entry_price + min_dist)
        if tp is not None:
            tp = min(tp, entry_price - min_dist)

    return roundp(sl), roundp(tp)

# -----------------------------
# Trading helpers
# -----------------------------
def _trading_enabled_guard(symbol: str) -> bool:
    """Ankstyvas guard’as AutoTrading/leidimų klausimui su aiškiu paaiškinimu."""
    try:
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        if not ti or not ai:
            _log(symbol, "not connected to terminal/account")
            return False
        if not getattr(ti, "trade_allowed", False):
            _log(symbol, "AutoTrading disabled in terminal. Enable 'Algo Trading' and allow in Tools→Options→Expert Advisors.")
            return False
        if getattr(ti, "tradeapi_disabled", False):
            _log(symbol, "Terminal trade API disabled by settings/policy")
            return False
        if not getattr(ai, "trade_allowed", False):
            _log(symbol, "Account trading not allowed (investor password/server).")
            return False
        return True
    except Exception:
        return True

def place_order(symbol: str, direction: str, volume: float, sl: float = None, tp: float = None,
                comment: str = "", atr: float = None):
    si = get_symbol_info(symbol)
    if not si:
        _log(symbol, "symbol_info() failed")
        return None
    if not si.visible:
        mt5.symbol_select(si.name, True)

    if not _trading_enabled_guard(si.name):
        return None

    if not _is_market_open(si):
        _log(symbol, "market seems closed (guard) – skipping order")
        return None

    if not _spread_ok(symbol):
        _log(symbol, "blocked by spread filter")
        return None

    tick = mt5.symbol_info_tick(si.name)
    if not tick:
        _log(symbol, "symbol_info_tick() failed")
        return None

    order_type = mt5.ORDER_TYPE_BUY if direction.lower() == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    modes = _broker_modes(symbol)
    _log(symbol, f"broker modes (try): {modes}")

    # MARKET attempts
    for mode in modes:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": si.name,
            "volume": float(volume),
            "type": order_type,
            "price": _normalize_price(symbol, price),
            "deviation": 30,
            "magic": MAGIC,
            "comment": comment[:31],
            "type_filling": mode,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if sl is not None:
            request["sl"] = _normalize_price(symbol, sl)
        if tp is not None:
            request["tp"] = _normalize_price(symbol, tp)

        # STOP'ų korekcija pagal realią įėjimo kainą
        sl_adj, tp_adj = _ensure_valid_stops(si, order_type, request["price"], request.get("sl"), request.get("tp"))
        if sl_adj is not None: request["sl"] = sl_adj
        if tp_adj is not None: request["tp"] = tp_adj

        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            _log(symbol, f"market SUCCESS mode {mode} @ {request['price']}")
            return res
        else:
            if DEBUG_MODE:
                _log(symbol, f"mode {mode} rejected: {getattr(res, 'comment', None)}")
            if getattr(res, "comment", "") and "AutoTrading disabled" in res.comment:
                _log(symbol, "Stop retrying: enable Algo Trading in MT5 and restart.")
                return None

    # PENDING LIMIT fallback (ATR-based buffer)
    buffer = max(20*si.point, (0.15 * atr) if atr else 0)
    limit_price = (price - buffer) if order_type == mt5.ORDER_TYPE_BUY else (price + buffer)

    for pending_fill in (mt5.ORDER_FILLING_RETURN, None):
        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": si.name,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY_LIMIT if order_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL_LIMIT,
            "price": _normalize_price(symbol, limit_price),
            "deviation": 30,
            "magic": MAGIC,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "expiration": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        }
        if sl is not None:
            req["sl"] = _normalize_price(symbol, sl)
        if tp is not None:
            req["tp"] = _normalize_price(symbol, tp)

        sl_adj, tp_adj = _ensure_valid_stops(si, order_type, req["price"], req.get("sl"), req.get("tp"))
        if sl_adj is not None: req["sl"] = sl_adj
        if tp_adj is not None: req["tp"] = tp_adj

        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            _log(symbol, f"limit SUCCESS @ {req['price']}")
            return res
        else:
            if DEBUG_MODE:
                fill_txt = "RETURN" if pending_fill else "default"
                _log(symbol, f"pending ({fill_txt}) rejected: {getattr(res,'comment',None)}")
            if getattr(res, "comment", "") and "AutoTrading disabled" in res.comment:
                _log(symbol, "Stop retrying: enable Algo Trading in MT5 and restart.")
                return None

    _log(symbol, "fatal: all orders failed")
    return None

def get_positions(symbol: str = None):
    try:
        if symbol:
            name = resolve_symbol(symbol) or symbol
            positions = mt5.positions_get(symbol=name)
        else:
            positions = mt5.positions_get()
        return [p for p in (positions or []) if getattr(p, "magic", 0) == MAGIC]
    except Exception:
        return []

# ---------- Orders helpers (for dedupe/guards) ----------
def get_orders(symbol: str = None):
    """Grąžina boto (MAGIC) pending orderius; jei nurodytas symbol – filtruoja pagal jį."""
    try:
        if symbol:
            name = resolve_symbol(symbol) or symbol
            orders = mt5.orders_get(symbol=name)
        else:
            orders = mt5.orders_get()
        return [o for o in (orders or []) if getattr(o, "magic", 0) == MAGIC]
    except Exception:
        return []

def cancel_order(ticket: int) -> bool:
    """Pašalina pending orderį pagal ticket."""
    try:
        req = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket)}
        res = mt5.order_send(req)
        return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
    except Exception:
        return False

def has_active_trade_or_order(symbol: str, direction: str | None = None) -> bool:
    """
    True jei tam simboliui jau yra boto atidaryta pozicija ARBA pending orderis.
    Jei direction ('buy'/'sell') nurodytas – tikrina tik tą pusę.
    """
    name = resolve_symbol(symbol) or symbol
    # Pozicijos
    try:
        for p in get_positions():
            if p.symbol == name:
                if direction is None:
                    return True
                if (direction == "buy" and p.type == mt5.POSITION_TYPE_BUY) or \
                   (direction == "sell" and p.type == mt5.POSITION_TYPE_SELL):
                    return True
    except Exception:
        pass
    # Pending orderiai
    try:
        for o in get_orders(name):
            is_buy = o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP)
            is_sell = o.type in (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP)
            if direction is None and (is_buy or is_sell):
                return True
            if (direction == "buy" and is_buy) or (direction == "sell" and is_sell):
                return True
    except Exception:
        pass
    return False

def close_position(ticket: int) -> bool:
    try:
        pos_list = mt5.positions_get(ticket=ticket)
        if not pos_list:
            return False
        p = pos_list[0]
        si = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if not (si and tick):
            return False
        order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
        for mode in _broker_modes(si.name):
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": si.name,
                "volume": float(p.volume),
                "position": ticket,
                "type": order_type,
                "price": _normalize_price(si.name, price),
                "deviation": 30,
                "magic": MAGIC,
                "type_filling": mode,
                "type_time": mt5.ORDER_TIME_GTC,
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return True
        return False
    except Exception:
        return False

def modify_sl(ticket: int, new_sl: float) -> bool:
    try:
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False
        symbol = pos[0].symbol
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": _normalize_price(symbol, new_sl),
            "magic": MAGIC,
        }
        res = mt5.order_send(req)
        return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
    except Exception:
        return False
