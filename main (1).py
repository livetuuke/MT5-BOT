# main.py – launcher
import sys, os, argparse
from datetime import datetime
from mt5_utils import initialize_mt5

def check_requirements():
    print("🔍 Checking requirements...")
    try:
        import MetaTrader5
        print("✅ MetaTrader5 ok")
    except ImportError:
        print("❌ pip install MetaTrader5")
        return False
    try:
        import pandas, numpy, ta, requests
        print("✅ All modules ok")
    except ImportError as e:
        print(f"❌ Missing: {e}")
        return False
    if not os.path.exists(".env"):
        with open(".env", "w") as f:
            f.write("TELEGRAM_TOKEN=\nTELEGRAM_CHAT_ID=\nDEBUG_MODE=False\nTEST_MODE=False\n")
        print("⚠️  .env template written – please edit it")
    return True

def main():
    parser = argparse.ArgumentParser(description="MT5 Scalping Bot")
    parser.add_argument("--test", action="store_true", help="Test mode")
    parser.add_argument("--check", action="store_true", help="Check deps only")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════╗\n║ MT5 SCALPING BOT – PRODUCTION READY          ║\n╚══════════════════════════════════════════════╝")

    if not check_requirements() and not args.check:
        sys.exit(1)
    if args.check:
        return
    if args.test:
        os.environ["TEST_MODE"] = "True"

    try:
        from bot import main as run_bot
        print(f"\n🚀 Starting bot at {datetime.now():%Y-%m-%d %H:%M:%S}")
        run_bot()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()