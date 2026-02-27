# config.py — aggressive + quality guards
import os, random
from datetime import time as _time
from dotenv import load_dotenv

load_dotenv()

# --- Core / env ---
MAGIC = int(os.getenv("MAGIC", random.randint(100000, 999999)))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or None
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or None

# Minimal equity guard (bot won't run below this)
MIN_EQUITY = float(os.getenv("MIN_EQUITY", "100.0"))

# --- Symbols & timeframe ---
TIMEFRAME = os.getenv("TIMEFRAME", "M1")
SYMBOLS   = [s.strip() for s in os.getenv("SYMBOLS", "EURUSD,GBPUSD,USDJPY").split(",")]

# --- Execution / throttling ---
SIGNAL_COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "20"))  # seconds per symbol
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "50"))

# --- Risk (percent of equity per trade) ---
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.001"))         # 0.1% per trade
MAX_DAILY_RISK = float(os.getenv("MAX_DAILY_RISK", "0.02"))       # 2%/day stop
MAX_DRAWDOWN_PERCENT = float(os.getenv("MAX_DRAWDOWN_PERCENT", "10.0"))

# --- SL/TP via ATR ---
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", "1.05"))
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", "1.60"))  # keep R > 1.3

# --- Signal tuning (aggressive mode ON) ---
AGGRESSIVE_MODE = (os.getenv("AGGRESSIVE_MODE", "true").lower() == "true")
MIN_SIGNAL_STRENGTH = float(os.getenv("MIN_SIGNAL_STRENGTH", "0.30"))

# VWAP pullback distance threshold in percent
VWAP_DISTANCE_THRESHOLD = float(os.getenv("VWAP_DISTANCE_THRESHOLD", "0.08"))  # 0.08%

# --- Sessions (terminal local time) ---
SESSIONS = {
    "LONDON": {"start": _time(8, 0),  "end": _time(12, 30)},
    "NY":     {"start": _time(13, 30), "end": _time(17, 30)},
    "ASIA":   {"start": _time(0, 0),  "end": _time(7, 30)},
}

# --- Per-symbol filters ---
SYMBOL_CONFIG = {
    "EURUSD": {
        "spread_filter": float(os.getenv("EURUSD_SPREAD", "1.5")),   # pips
        "min_atr":       float(os.getenv("EURUSD_MIN_ATR", "0.00008")),
        "session_boost": {"london": 1.4, "ny": 1.3, "asia": 0.8},
        "risk_multiplier": 1.00,
    },
    "GBPUSD": {
        "spread_filter": float(os.getenv("GBPUSD_SPREAD", "2.0")),
        "min_atr":       float(os.getenv("GBPUSD_MIN_ATR", "0.00010")),
        "session_boost": {"london": 1.5, "ny": 1.2, "asia": 0.7},
        "risk_multiplier": 0.90,
    },
    "USDJPY": {
        "spread_filter": float(os.getenv("USDJPY_SPREAD", "1.5")),
        "min_atr":       float(os.getenv("USDJPY_MIN_ATR", "0.008")),
        "session_boost": {"london": 1.1, "ny": 1.3, "asia": 1.4},
        "risk_multiplier": 1.10,
    },
}

# --- Misc ---
DEBUG_MODE = (os.getenv("DEBUG_MODE", "false").lower() == "true")
TEST_MODE  = (os.getenv("TEST_MODE",  "false").lower() == "true")

# Ensure logs dir exists
os.makedirs("logs", exist_ok=True)
