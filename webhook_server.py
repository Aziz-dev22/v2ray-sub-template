"""Receives irMarket's order.updated webhook callbacks.

irMarket signs every webhook request with:
    X-Signature: HMAC-SHA256(secret, raw_body)
The secret is whatever was returned when /api/buyer/webhook was registered
(see bot.py -> admin_setwebhook), and is stored in the settings table.
"""
import hmac
import hashlib
import json
import logging

from fastapi import FastAPI, Request, HTTPException
from telegram import Bot
from telegram.constants import ParseMode

import config
import database as db
import helpers

logger = logging.getLogger("webhook_server")

app = FastAPI(title="irMarket webhook receiver")

# Set by main.py once the bot's Application is created, so we can push
# Telegram notifications from within this HTTP server.
bot_instance: Bot | None = None


def set_bot(bot: Bot) -> None:
    global bot_instance
    bot_instance = bot


async def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = await db.get_setting("webhook_secret")
    if not secret:
        # No secret on file yet (webhook never registered) - reject to be safe.
        return False
    if not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post(config.WEBHOOK_PATH)
async def irmarket_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Signature")

    if not await _verify_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid json")

    if payload.get("event") != "order.updated":
        return {"ok": True}  # ignore anything we don't understand

    irmarket_order_id = payload.get("order_id")
    status = payload.get("status")
    accounts = payload.get("accounts")
    if irmarket_order_id is None or status is None:
        raise HTTPException(status_code=400, detail="missing order_id/status")

    accounts_json = json.dumps(accounts, ensure_ascii=False) if accounts else None
    order = await db.update_order_status_by_irmarket_id(irmarket_order_id, status, accounts_json)

    if order and bot_instance:
        await _notify_user(order, status, accounts_json)

    return {"ok": True}


async def _notify_user(order, status: str, accounts_json: str | None) -> None:
    text = (
        f"📦 بروزرسانی سفارش #{order['id']} ({order['product_name']})\n"
        f"وضعیت جدید: {helpers.status_fa(status)}"
    )
    if status == "delivered" and accounts_json:
        text += f"\n\n🔑 مشخصات:\n{helpers.fmt_accounts(accounts_json)}"
    try:
        await bot_instance.send_message(
            order["telegram_id"], text, parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning("Could not notify user %s: %s", order["telegram_id"], e)


@app.get("/health")
async def health():
    return {"status": "ok"}
