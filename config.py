"""Central configuration, loaded from environment variables / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_TELEGRAM_IDS = _get_admin_ids()

IRMARKET_BASE_URL = os.getenv("IRMARKET_BASE_URL", "https://api.irmarket.store").rstrip("/")
IRMARKET_API_KEY = os.getenv("IRMARKET_API_KEY", "")

PRICE_MARKUP = float(os.getenv("PRICE_MARKUP", "1.30"))

DEMO_CREDIT_AMOUNT = float(os.getenv("DEMO_CREDIT_AMOUNT", "50"))
DEMO_CREDIT_COOLDOWN_HOURS = float(os.getenv("DEMO_CREDIT_COOLDOWN_HOURS", "24"))

WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "").rstrip("/")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
WEBHOOK_PATH = "/webhook/irmarket"

DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")


def validate() -> list[str]:
    """Return a list of human readable problems with the current config."""
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN تنظیم نشده است.")
    if not IRMARKET_API_KEY:
        problems.append("IRMARKET_API_KEY تنظیم نشده است.")
    if not ADMIN_TELEGRAM_IDS:
        problems.append("ADMIN_TELEGRAM_IDS خالی است - هیچ ادمینی به پنل دسترسی نخواهد داشت.")
    return problems
