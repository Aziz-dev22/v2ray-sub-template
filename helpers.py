import json
import time
import config


def retail_price(price_usd: float, markup: float | None = None) -> float:
    """What YOUR bot charges its own users, computed from irMarket's price_usd.

    `markup` normally comes from the live, admin-editable value (bot.current_markup).
    Falls back to the .env default only if not provided.
    """
    m = markup if markup is not None else config.PRICE_MARKUP
    return round(price_usd * m, 2)


def markup_to_percent(markup: float) -> float:
    return round((markup - 1) * 100, 2)


def percent_to_markup(percent: float) -> float:
    return round(1 + percent / 100, 4)


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
