import json
import time
import config


def retail_price(price_usd: float) -> float:
    """What YOUR bot charges its own users, computed from irMarket's price_usd."""
    return round(price_usd * config.PRICE_MARKUP, 2)


def fmt_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def fmt_accounts(accounts_json: str | None) -> str:
    if not accounts_json:
        return "—"
    try:
        accounts = json.loads(accounts_json)
    except (TypeError, ValueError):
        return str(accounts_json)
    return "\n".join(f"• `{a}`" for a in accounts)


def new_idempotency_key(telegram_id: int) -> str:
    return f"tgbot-{telegram_id}-{int(time.time() * 1000)}"


STATUS_LABELS_FA = {
    "processing": "⏳ در حال پردازش",
    "delivered": "✅ تحویل شد",
    "failed": "❌ ناموفق",
    "cancelled": "🚫 لغو شد",
}


def status_fa(status: str) -> str:
    return STATUS_LABELS_FA.get(status, status)
