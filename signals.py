import pandas as pd
import numpy as np
import ta
from datetime import datetime, timedelta
from config import *

# --------- Indicator calc ---------
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    if len(df) < 50:
        return df

    # EMAs
    df["ema_fast"] = ta.trend.ema_indicator(df["close"], 8)
    df["ema_mid"]  = ta.trend.ema_indicator(df["close"], 21)
    df["ema_slow"] = ta.trend.ema_indicator(df["close"], 50)

    # RSI / MACD
    df["rsi"] = ta.momentum.rsi(df["close"], 14)
    macd = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # ATR
    df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)

    # -------- Daily-reset VWAP (be .apply) --------
    # tipinės kainos ir apimties serijos
    tp  = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["tick_volume"].replace(0, 1)

    # grupuojam pagal kalendorinę dieną (terminalo laikas)
    day = df["time"].dt.date

    # kumuliacinės skaitiklio ir vardiklio sumos kiekvienai dienai
    num = (tp * vol).groupby(day).cumsum()
    den = vol.groupby(day).cumsum()

    df["vwap"] = num / den
    df["vwap_dist"] = (df["close"] - df["vwap"]) / df["vwap"] * 100.0
    # ----------------------------------------------

    # Bollinger (kontekstui)
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_up"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    return df

# --------- Helpers ---------
def _trend_ok(row) -> tuple[bool, str]:
    if pd.isna(row["ema_fast"]) or pd.isna(row["ema_mid"]) or pd.isna(row["ema_slow"]):
        return False, "ema_nan"
    bull = row["ema_fast"] > row["ema_mid"] > row["ema_slow"]
    bear = row["ema_fast"] < row["ema_mid"] < row["ema_slow"]
    if bull:
        return True, "bull_trend"
    if bear:
        return True, "bear_trend"
    return False, "no_trend"

def _momentum_side(row) -> tuple[str | None, str | None]:
    if pd.isna(row["macd"]) or pd.isna(row["macd_signal"]):
        return None, None
    if row["macd"] > row["macd_signal"]:
        return "buy", "MACD momentum"
    if row["macd"] < row["macd_signal"]:
        return "sell", "MACD momentum"
    return None, None

def _vwap_vote(row) -> tuple[str|None, float, str]:
    # VWAP distance slenkstis procentais; vwap_dist jau procentais
    vwap_ok  = abs(row["vwap_dist"]) >= (VWAP_DISTANCE_THRESHOLD * 0.80)  # 20% lengviau
    touch_ok = (row["low"] <= row["vwap"] <= row["high"])
    if (vwap_ok or touch_ok):
        if row["close"] <= row["vwap"] and row["rsi"] < 38:
            return "buy", (0.66 if vwap_ok else 0.60), "VWAP pullback/touch (buy)"
        if row["close"] >= row["vwap"] and row["rsi"] > 62:
            return "sell", (0.66 if vwap_ok else 0.60), "VWAP pullback/touch (sell)"
    return None, 0.0, ""

def _atr_ok(row, symbol: str) -> bool:
    cfg = SYMBOL_CONFIG.get(symbol, {})
    min_atr = cfg.get("min_atr", 0.0)
    return (not pd.isna(row["atr"])) and (row["atr"] >= min_atr)

def _spread_ok(symbol: str) -> bool:
    try:
        import MetaTrader5 as mt5
        si = mt5.symbol_info(symbol)
        if not si:
            return True
        tk = mt5.symbol_info_tick(symbol)
        if not tk:
            return True
        spread_points = (tk.ask - tk.bid) / si.point
        pips = spread_points / (10 if si.digits in (3,5) else 1)
        max_pips = SYMBOL_CONFIG.get(symbol, {}).get("spread_filter", 2.0)
        return pips <= max_pips
    except Exception:
        return True

def _anti_chop_ok(df: pd.DataFrame) -> bool:
    # paskutinių 20 barų avg range / ATR turi būti >= 0.7
    if len(df) < 30:
        return True
    last = df.tail(20)
    avg_range = (last["high"] - last["low"]).mean()
    atr = last["atr"].iloc[-1]
    if pd.isna(atr) or atr == 0:
        return True
    return (avg_range / atr) >= 0.7

def check_session(now: datetime) -> tuple[bool, float]:
    # paprastai: leisti, boost 1.0 (palikta suderinamumui)
    return True, 1.0

# --------- Main signal generator ---------
def get_signals(df: pd.DataFrame, symbol: str):
    if df is None or df.empty or len(df) < 50:
        return None

    if not _anti_chop_ok(df):
        return None

    last = df.iloc[-1]
    ok_trend, trend_tag = _trend_ok(last)
    if not ok_trend:
        return None
    if not _atr_ok(last, symbol):
        return None
    if not _spread_ok(symbol):
        return None

    votes = []
    reasons = []

    side, weight, reason = _vwap_vote(last)
    if side:
        votes.append((side, weight))
        reasons.append(reason)

    mom_side, mom_reason = _momentum_side(last)
    if mom_side:
        votes.append((mom_side, 0.55))
        reasons.append(mom_reason)

    # EMA pullback bonus
    if last["ema_fast"] > last["ema_mid"] and last["close"] >= last["ema_mid"]:
        votes.append(("buy", 0.50))
        reasons.append("EMA pullback")
    elif last["ema_fast"] < last["ema_mid"] and last["close"] <= last["ema_mid"]:
        votes.append(("sell", 0.50))
        reasons.append("EMA pullback")

    buy_score  = sum(w for s, w in votes if s == "buy")
    sell_score = sum(w for s, w in votes if s == "sell")

    min_single = MIN_SIGNAL_STRENGTH if AGGRESSIVE_MODE else 0.65

    if buy_score >= sell_score and buy_score >= min_single:
        signal_type, strength = "buy", float(buy_score)
    elif sell_score > buy_score and sell_score >= min_single:
        signal_type, strength = "sell", float(sell_score)
    else:
        return None

    return {
        "type": signal_type,
        "strength": round(strength, 2),
        "atr": float(last["atr"]) if not pd.isna(last["atr"]) else None,
        "reasons": reasons,
    }
