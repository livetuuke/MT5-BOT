import csv, os, time, threading, requests
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

_LOCK = threading.Lock()
_LAST_TG = 0

def tg(message: str):
    global _LAST_TG
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TG: {message}")
        return
    now = time.time()
    if now - _LAST_TG < 2:
        time.sleep(2 - (now - _LAST_TG))
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
        _LAST_TG = time.time()
    except Exception:
        print(f"TG Failed: {message}")

def log_trade(action, symbol, trade_type, price, volume, profit=0, reason=""):
    with _LOCK:
        try:
            file_path = "logs/trades.csv"
            file_exists = os.path.exists(file_path)
            with open(file_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "action", "symbol", "type", "price", "volume", "profit", "reason"])
                writer.writerow([datetime.now().isoformat(), action, symbol, trade_type, price, volume, profit, reason])
        except Exception as e:
            print(f"Log trade error: {e}")

def log_error(context, error):
    msg = f"ERROR [{context}]: {error}"
    print(msg)
    tg(f"❌ {msg}")
    try:
        with _LOCK:
            with open("logs/errors.log", "a") as f:
                f.write(f"{datetime.now().isoformat()} – {msg}\n")
    except:
        pass

def get_trade_stats():
    import pandas as pd, os
    path = "logs/trades.csv"
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_csv(path)
        closed = df[df["action"] == "CLOSE"]
        if closed.empty:
            return {}
        wins = closed[closed["profit"] > 0]
        return {
            "total_trades": len(closed),
            "win_rate": len(wins) / len(closed) * 100,
            "total_profit": closed["profit"].sum(),
        }
    except:
        return {}