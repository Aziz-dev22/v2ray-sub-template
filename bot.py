"""Telegram bot that resells irMarket products through a local demo wallet."""
from __future__ import annotations

import json
import logging
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import database as db
import helpers
from irmarket_client import client, IrMarketError

logger = logging.getLogger("bot")

# In-memory cache of the product catalogue (refreshed on demand / TTL).
_products_cache: list[dict] = []
_products_cache_ts: float = 0
PRODUCTS_TTL_SECONDS = 120

# Live, admin-editable profit markup. Loaded from the DB at startup (falls
# back to PRICE_MARKUP from .env the very first time the bot ever runs).
current_markup: float = config.PRICE_MARKUP


async def load_markup() -> None:
    """Call once at startup to pull the persisted markup (if any) from the DB."""
    global current_markup
    stored = await db.get_setting("price_markup")
    if stored:
        try:
            current_markup = float(stored)
        except ValueError:
            pass


async def set_markup(new_markup: float) -> None:
    global current_markup
    current_markup = new_markup
    await db.set_setting("price_markup", str(new_markup))


def is_admin(telegram_id: int) -> bool:
    return telegram_id in config.ADMIN_TELEGRAM_IDS


# ---------------------------------------------- product settings & categories --
# product_id -> {"enabled": bool, "markup": float|None, "category_id": int|None}
_product_settings_cache: dict[int, dict] = {}
# category_id -> name
_categories_cache: dict[int, str] = {}

_DEFAULT_PRODUCT_SETTING = {"enabled": True, "markup": None, "category_id": None}


async def load_product_settings() -> None:
    global _product_settings_cache
    rows = await db.get_all_product_settings()
    _product_settings_cache = {
        pid: {"enabled": bool(r["enabled"]), "markup": r["markup"], "category_id": r["category_id"]}
        for pid, r in rows.items()
    }


async def load_categories() -> None:
    global _categories_cache
    rows = await db.list_categories()
    _categories_cache = {r["id"]: r["name"] for r in rows}


def get_product_setting(product_id: int) -> dict:
    return _product_settings_cache.get(product_id, _DEFAULT_PRODUCT_SETTING)


def _update_product_cache(product_id: int, **kwargs) -> None:
    s = dict(_product_settings_cache.get(product_id, _DEFAULT_PRODUCT_SETTING))
    s.update(kwargs)
    _product_settings_cache[product_id] = s


def effective_markup(product_id: int) -> float:
    s = get_product_setting(product_id)
    return s["markup"] if s["markup"] is not None else current_markup


def category_name(category_id: int | None) -> str:
    if category_id is None:
        return "بدون دسته"
    return _categories_cache.get(category_id, f"#{category_id}")


async def get_products(force: bool = False) -> list[dict]:
    global _products_cache, _products_cache_ts
    if force or not _products_cache or (time.time() - _products_cache_ts) > PRODUCTS_TTL_SECONDS:
        _products_cache = await client.get_products()
        _products_cache_ts = time.time()
    return _products_cache


def find_product(product_id: int) -> dict | None:
    for p in _products_cache:
        if p.get("id") == product_id:
            return p
    return None


# --------------------------------------------------------------- menus ----
def main_menu_kb(telegram_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🛍 محصولات", callback_data="menu:products")],
        [
            InlineKeyboardButton("💰 کیف پول من", callback_data="menu:wallet"),
            InlineKeyboardButton("📦 سفارش‌های من", callback_data="menu:orders"),
        ],
    ]
    if is_admin(telegram_id):
        rows.append([InlineKeyboardButton("⚙️ پنل ادمین", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def back_kb(target: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data=target)]])


# ------------------------------------------------------------ commands ----
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.get_or_create_user(user.id, user.username)
    text = (
        f"سلام {user.first_name} 👋\n\n"
        "به فروشگاه خوش اومدی. از منوی زیر می‌تونی محصولات رو ببینی، کیف پولت رو "
        "شارژ کنی (نسخه‌ی آزمایشی) و خرید انجام بدی."
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb(user.id))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "دستورات:\n"
        "/start - منوی اصلی\n"
        "/products - لیست محصولات\n"
        "/wallet - موجودی کیف پول\n"
        "/orders - سفارش‌های من\n"
        + ("/admin - پنل مدیریت\n" if is_admin(update.effective_user.id) else "")
    )


# ------------------------------------------------------------- routing ----
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    telegram_id = update.effective_user.id
    await db.get_or_create_user(telegram_id, update.effective_user.username)

    if data == "menu:main":
        await query.edit_message_text(
            "منوی اصلی:", reply_markup=main_menu_kb(telegram_id)
        )
    elif data == "menu:products":
        await show_categories(query)
    elif data.startswith("cat:"):
        category_key = data.split(":", 1)[1]
        await show_products(query, category_key=category_key, page=0)
    elif data.startswith("products_page:"):
        _, category_key, page_str = data.split(":")
        await show_products(query, category_key=category_key, page=int(page_str))
    elif data.startswith("product:"):
        product_id = int(data.split(":")[1])
        await show_product_detail(query, product_id)
    elif data.startswith("qty:"):
        _, product_id, qty = data.split(":")
        await show_product_detail(query, int(product_id), qty=int(qty))
    elif data.startswith("buy:"):
        _, product_id, qty = data.split(":")
        await do_purchase(query, context, int(product_id), int(qty))
    elif data == "menu:wallet":
        await show_wallet(query, telegram_id)
    elif data == "demo:credit":
        await claim_demo_credit(query, telegram_id)
    elif data == "menu:orders":
        await show_orders(query, telegram_id)
    elif data.startswith("order:"):
        order_id = int(data.split(":")[1])
        await show_order_detail(query, order_id)
    elif data == "menu:admin" and is_admin(telegram_id):
        await show_admin_menu(query)
    elif data == "admin:stats" and is_admin(telegram_id):
        await show_admin_stats(query)
    elif data == "admin:credit" and is_admin(telegram_id):
        context.user_data["pending_action"] = "admin_credit"
        await query.edit_message_text(
            "آیدی عددی تلگرام کاربر و مبلغ رو اینطوری بفرست:\n`123456789 25`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_kb("menu:admin"),
        )
    elif data == "admin:setwebhook" and is_admin(telegram_id):
        context.user_data["pending_action"] = "admin_setwebhook"
        await query.edit_message_text(
            "URL عمومی و HTTPS سرور خودت رو بفرست (مثلا https://yourdomain.com).\n"
            "این آدرس در irMarket به عنوان webhook ثبت می‌شه و باید مسیر "
            f"`{config.WEBHOOK_PATH}` روش در دسترس باشه.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_kb("menu:admin"),
        )
    elif data == "admin:recent" and is_admin(telegram_id):
        await show_admin_recent_orders(query)
    elif data == "admin:markup" and is_admin(telegram_id):
        context.user_data["pending_action"] = "admin_set_markup"
        percent_now = helpers.markup_to_percent(current_markup)
        await query.edit_message_text(
            f"درصد سود فعلی: {percent_now}%\n\n"
            "درصد سود جدید رو به‌صورت عدد بفرست (مثلا `30` یعنی ۳۰٪ سود روی "
            "قیمت خرید از irMarket؛ `0` یعنی بدون سود).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_kb("menu:admin"),
        )
    elif data.startswith("adminprod:list:") and is_admin(telegram_id):
        page = int(data.split(":")[2])
        await show_admin_products(query, page)
    elif data.startswith("adminprod:view:") and is_admin(telegram_id):
        product_id = int(data.split(":")[2])
        await show_admin_product_detail(query, product_id)
    elif data.startswith("adminprod:toggle:") and is_admin(telegram_id):
        product_id = int(data.split(":")[2])
        s = get_product_setting(product_id)
        new_enabled = not s["enabled"]
        await db.set_product_enabled(product_id, new_enabled)
        _update_product_cache(product_id, enabled=new_enabled)
        await show_admin_product_detail(query, product_id)
    elif data.startswith("adminprod:setcat:") and is_admin(telegram_id):
        product_id = int(data.split(":")[2])
        context.user_data["pending_action"] = "admin_product_category"
        context.user_data["pending_product_id"] = product_id
        cats = ", ".join(_categories_cache.values()) or "—"
        await query.edit_message_text(
            f"اسم دسته‌بندی رو بفرست (دسته‌های فعلی: {cats}).\n"
            "برای حذف دسته‌بندی این محصول، بنویس: `-`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_kb(f"adminprod:view:{product_id}"),
        )
    elif data.startswith("adminprod:setmarkup:") and is_admin(telegram_id):
        product_id = int(data.split(":")[2])
        context.user_data["pending_action"] = "admin_product_markup"
        context.user_data["pending_product_id"] = product_id
        s = get_product_setting(product_id)
        current_note = (
            f"سود اختصاصی فعلی: {helpers.markup_to_percent(s['markup'])}%"
            if s["markup"] is not None
            else "الان از سود عمومی استفاده می‌کنه."
        )
        await query.edit_message_text(
            f"{current_note}\n\nدرصد سود اختصاصی این محصول رو بفرست (مثلا `40`).\n"
            "برای برگشت به سود عمومی، بنویس: `-`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_kb(f"adminprod:view:{product_id}"),
        )


# ------------------------------------------------------------ products ----
PAGE_SIZE = 6


def _enabled_products() -> list[dict]:
    return [p for p in _products_cache if get_product_setting(p["id"])["enabled"]]


async def show_categories(query):
    try:
        await get_products()
    except IrMarketError as e:
        await query.edit_message_text(f"خطا در دریافت محصولات: {e.message}", reply_markup=back_kb())
        return

    enabled = _enabled_products()
    if not enabled:
        await query.edit_message_text("در حال حاضر محصولی برای فروش موجود نیست.", reply_markup=back_kb())
        return

    used_category_ids: dict[int, int] = {}  # category_id -> count
    uncategorized_count = 0
    for p in enabled:
        cat_id = get_product_setting(p["id"])["category_id"]
        if cat_id is None:
            uncategorized_count += 1
        else:
            used_category_ids[cat_id] = used_category_ids.get(cat_id, 0) + 1

    # If nothing has been categorised yet, skip straight to the flat product list.
    if not used_category_ids:
        await show_products(query, category_key="all", page=0)
        return

    rows = [[InlineKeyboardButton("🗂 همه محصولات", callback_data="cat:all")]]
    for cat_id, count in sorted(used_category_ids.items(), key=lambda kv: category_name(kv[0])):
        label = f"{category_name(cat_id)} ({count})"
        rows.append([InlineKeyboardButton(label, callback_data=f"cat:{cat_id}")])
    if uncategorized_count:
        rows.append(
            [InlineKeyboardButton(f"بدون دسته ({uncategorized_count})", callback_data="cat:none")]
        )
    rows.append([InlineKeyboardButton("⬅️ منوی اصلی", callback_data="menu:main")])

    await query.edit_message_text("🛍 یک دسته‌بندی رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows))


def _products_in_category(category_key: str) -> list[dict]:
    enabled = _enabled_products()
    if category_key == "all":
        return enabled
    if category_key == "none":
        return [p for p in enabled if get_product_setting(p["id"])["category_id"] is None]
    try:
        cat_id = int(category_key)
    except ValueError:
        return enabled
    return [p for p in enabled if get_product_setting(p["id"])["category_id"] == cat_id]


async def show_products(query, category_key: str, page: int):
    try:
        await get_products()
    except IrMarketError as e:
        await query.edit_message_text(f"خطا در دریافت محصولات: {e.message}", reply_markup=back_kb())
        return

    products = _products_in_category(category_key)
    if not products:
        await query.edit_message_text("محصولی در این دسته‌بندی نیست.", reply_markup=back_kb("menu:products"))
        return

    start = page * PAGE_SIZE
    chunk = products[start : start + PAGE_SIZE]

    rows = []
    for p in chunk:
        price = helpers.retail_price(p["price_usd"], effective_markup(p["id"]))
        label = f"{p['name']} — {helpers.fmt_usd(price)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"product:{p['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"products_page:{category_key}:{page-1}"))
    if start + PAGE_SIZE < len(products):
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"products_page:{category_key}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ دسته‌بندی‌ها", callback_data="menu:products")])

    title = "🛍 محصولات"
    if category_key not in ("all",):
        cat_label = "بدون دسته" if category_key == "none" else category_name(int(category_key))
        title = f"🛍 {cat_label}"
    await query.edit_message_text(
        f"{title} (صفحه {page+1}):", reply_markup=InlineKeyboardMarkup(rows)
    )


async def show_product_detail(query, product_id: int, qty: int = 1):
    product = find_product(product_id)
    if not product:
        # cache might be stale
        await get_products(force=True)
        product = find_product(product_id)
    if not product:
        await query.edit_message_text("این محصول دیگه در دسترس نیست.", reply_markup=back_kb("menu:products"))
        return
    if not get_product_setting(product_id)["enabled"]:
        await query.edit_message_text("این محصول موقتاً غیرفعال شده.", reply_markup=back_kb("menu:products"))
        return

    unit_price = helpers.retail_price(product["price_usd"], effective_markup(product_id))
    total = round(unit_price * qty, 2)
    stock_note = ""
    if "stock" in product:
        stock_note = f"\nموجودی: {product['stock']}"

    text = (
        f"📦 *{product['name']}*\n"
        f"قیمت واحد: {helpers.fmt_usd(unit_price)}\n"
        f"تعداد: {qty}\n"
        f"مبلغ کل: {helpers.fmt_usd(total)}"
        f"{stock_note}"
    )
    rows = [
        [
            InlineKeyboardButton("➖", callback_data=f"qty:{product_id}:{max(1, qty-1)}"),
            InlineKeyboardButton(str(qty), callback_data=f"qty:{product_id}:{qty}"),
            InlineKeyboardButton("➕", callback_data=f"qty:{product_id}:{qty+1}"),
        ],
        [InlineKeyboardButton("✅ خرید", callback_data=f"buy:{product_id}:{qty}")],
        [InlineKeyboardButton("⬅️ لیست محصولات", callback_data="menu:products")],
    ]
    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows)
    )


async def do_purchase(query, context: ContextTypes.DEFAULT_TYPE, product_id: int, qty: int):
    telegram_id = query.from_user.id
    product = find_product(product_id) or (await get_products(force=True), find_product(product_id))[1]
    if not product:
        await query.edit_message_text("این محصول دیگه در دسترس نیست.", reply_markup=back_kb("menu:products"))
        return
    if not get_product_setting(product_id)["enabled"]:
        await query.edit_message_text("این محصول موقتاً غیرفعال شده.", reply_markup=back_kb("menu:products"))
        return

    unit_price = helpers.retail_price(product["price_usd"], effective_markup(product_id))
    total = round(unit_price * qty, 2)

    user = await db.get_user(telegram_id)
    if user["wallet_usd"] < total:
        await query.edit_message_text(
            f"موجودی کیف پول کافی نیست.\nموجودی فعلی: {helpers.fmt_usd(user['wallet_usd'])}\n"
            f"مبلغ لازم: {helpers.fmt_usd(total)}\n\n"
            "چون فعلاً درگاه پرداخت واقعی وصل نیست، می‌تونی از بخش «کیف پول من» "
            "اعتبار آزمایشی بگیری.",
            reply_markup=back_kb("menu:wallet"),
        )
        return

    idem_key = helpers.new_idempotency_key(telegram_id)
    order_id = await db.create_order(
        telegram_id, product_id, product["name"], qty, unit_price, idem_key
    )

    # Deduct first (demo wallet) - if purchase fails we refund.
    await db.adjust_wallet(telegram_id, -total)

    await query.edit_message_text("⏳ در حال ثبت سفارش نزد تامین‌کننده...")

    try:
        result = await client.purchase(
            product_id=product_id, quantity=qty, idempotency_key=idem_key
        )
    except IrMarketError as e:
        await db.adjust_wallet(telegram_id, total)  # refund
        await db.update_order_result(order_id, status="failed")
        await query.edit_message_text(
            f"❌ خرید ناموفق بود: {e.message}\nمبلغ به کیف پولت برگشت داده شد.",
            reply_markup=back_kb("menu:products"),
        )
        return

    status = result.get("status", "processing")
    irmarket_order_id = result.get("order_id")
    accounts = result.get("accounts")
    accounts_json = json.dumps(accounts, ensure_ascii=False) if accounts else None

    await db.update_order_result(
        order_id, status=status, irmarket_order_id=irmarket_order_id, accounts_json=accounts_json
    )

    if status == "failed":
        refunded = result.get("refunded", False)
        if refunded:
            await db.adjust_wallet(telegram_id, total)
        msg = "❌ سفارش ناموفق بود." + (" مبلغ به کیف پول برگشت داده شد." if refunded else "")
        await query.edit_message_text(msg, reply_markup=back_kb("menu:products"))
        return

    text = f"سفارش شماره {order_id} ثبت شد.\nوضعیت: {helpers.status_fa(status)}"
    if status == "delivered":
        text += f"\n\n🔑 مشخصات:\n{helpers.fmt_accounts(accounts_json)}"
    else:
        text += "\n\nهنوز در حال پردازشه؛ از «سفارش‌های من» می‌تونی وضعیتش رو چک کنی."

    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb("menu:orders")
    )


# --------------------------------------------------------------- wallet ----
async def show_wallet(query, telegram_id: int):
    user = await db.get_user(telegram_id)
    can_claim = True
    wait_hint = ""
    if user["last_demo_credit_ts"]:
        elapsed_h = (time.time() - user["last_demo_credit_ts"]) / 3600
        if elapsed_h < config.DEMO_CREDIT_COOLDOWN_HOURS:
            can_claim = False
            remaining = config.DEMO_CREDIT_COOLDOWN_HOURS - elapsed_h
            wait_hint = f"\n(تا شارژ بعدی {remaining:.1f} ساعت مونده)"

    text = (
        f"💰 موجودی کیف پول: {helpers.fmt_usd(user['wallet_usd'])}\n\n"
        "این نسخه‌ی آزمایشیه و درگاه پرداخت واقعی وصل نشده. برای تست می‌تونی "
        "اعتبار آزمایشی رایگان بگیری." + wait_hint
    )
    rows = []
    if can_claim:
        rows.append(
            [InlineKeyboardButton(
                f"🎁 دریافت {helpers.fmt_usd(config.DEMO_CREDIT_AMOUNT)} اعتبار آزمایشی",
                callback_data="demo:credit",
            )]
        )
    rows.append([InlineKeyboardButton("⬅️ منوی اصلی", callback_data="menu:main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def claim_demo_credit(query, telegram_id: int):
    user = await db.get_user(telegram_id)
    if user["last_demo_credit_ts"]:
        elapsed_h = (time.time() - user["last_demo_credit_ts"]) / 3600
        if elapsed_h < config.DEMO_CREDIT_COOLDOWN_HOURS:
            await show_wallet(query, telegram_id)
            return
    await db.adjust_wallet(telegram_id, config.DEMO_CREDIT_AMOUNT)
    await db.mark_demo_credit_claimed(telegram_id)
    await show_wallet(query, telegram_id)


# --------------------------------------------------------------- orders ----
async def show_orders(query, telegram_id: int):
    orders = await db.get_user_orders(telegram_id)
    if not orders:
        await query.edit_message_text("هنوز سفارشی ثبت نکردی.", reply_markup=back_kb())
        return
    rows = []
    for o in orders:
        label = f"#{o['id']} {o['product_name']} — {helpers.status_fa(o['status'])}"
        rows.append([InlineKeyboardButton(label, callback_data=f"order:{o['id']}")])
    rows.append([InlineKeyboardButton("⬅️ منوی اصلی", callback_data="menu:main")])
    await query.edit_message_text("📦 سفارش‌های من:", reply_markup=InlineKeyboardMarkup(rows))


async def show_order_detail(query, order_id: int):
    order = await db.get_order(order_id)
    if not order or order["telegram_id"] != query.from_user.id:
        await query.edit_message_text("سفارش پیدا نشد.", reply_markup=back_kb("menu:orders"))
        return

    # If still processing, try a live refresh from irMarket.
    if order["status"] == "processing" and order["irmarket_order_id"]:
        try:
            fresh = await client.get_order(order["irmarket_order_id"])
            new_status = fresh.get("status", order["status"])
            accounts = fresh.get("accounts")
            accounts_json = json.dumps(accounts, ensure_ascii=False) if accounts else None
            if new_status != order["status"] or accounts_json:
                await db.update_order_result(order_id, status=new_status, accounts_json=accounts_json)
                order = await db.get_order(order_id)
        except IrMarketError:
            pass

    text = (
        f"سفارش #{order['id']}\n"
        f"محصول: {order['product_name']}\n"
        f"تعداد: {order['quantity']}\n"
        f"مبلغ کل: {helpers.fmt_usd(order['total_price_usd'])}\n"
        f"وضعیت: {helpers.status_fa(order['status'])}\n"
    )
    if order["status"] == "delivered":
        text += f"\n🔑 مشخصات:\n{helpers.fmt_accounts(order['accounts'])}"

    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb("menu:orders")
    )


# ---------------------------------------------------------------- admin ----
async def show_admin_menu(query):
    percent_now = helpers.markup_to_percent(current_markup)
    rows = [
        [InlineKeyboardButton("📊 آمار", callback_data="admin:stats")],
        [InlineKeyboardButton("🗂 مدیریت محصولات", callback_data="adminprod:list:0")],
        [InlineKeyboardButton(f"📈 درصد سود عمومی ({percent_now}%)", callback_data="admin:markup")],
        [InlineKeyboardButton("💵 شارژ کیف پول کاربر", callback_data="admin:credit")],
        [InlineKeyboardButton("🧾 سفارش‌های اخیر", callback_data="admin:recent")],
        [InlineKeyboardButton("🔗 تنظیم Webhook", callback_data="admin:setwebhook")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="menu:main")],
    ]
    await query.edit_message_text("⚙️ پنل ادمین:", reply_markup=InlineKeyboardMarkup(rows))


ADMIN_PAGE_SIZE = 8


async def show_admin_products(query, page: int = 0):
    try:
        products = await get_products()
    except IrMarketError as e:
        await query.edit_message_text(f"خطا در دریافت محصولات: {e.message}", reply_markup=back_kb("menu:admin"))
        return

    if not products:
        await query.edit_message_text("محصولی یافت نشد.", reply_markup=back_kb("menu:admin"))
        return

    start = page * ADMIN_PAGE_SIZE
    chunk = products[start : start + ADMIN_PAGE_SIZE]

    rows = []
    for p in chunk:
        s = get_product_setting(p["id"])
        icon = "✅" if s["enabled"] else "🚫"
        label = f"{icon} {p['name']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"adminprod:view:{p['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"adminprod:list:{page-1}"))
    if start + ADMIN_PAGE_SIZE < len(products):
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"adminprod:list:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ پنل ادمین", callback_data="menu:admin")])

    await query.edit_message_text(
        f"🗂 مدیریت محصولات (صفحه {page+1}) — روی هر محصول بزن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def show_admin_product_detail(query, product_id: int):
    product = find_product(product_id)
    if not product:
        await get_products(force=True)
        product = find_product(product_id)
    if not product:
        await query.edit_message_text("این محصول دیگه در کاتالوگ نیست.", reply_markup=back_kb("adminprod:list:0"))
        return

    s = get_product_setting(product_id)
    eff_markup = effective_markup(product_id)
    retail = helpers.retail_price(product["price_usd"], eff_markup)
    markup_line = (
        f"سود اختصاصی: {helpers.markup_to_percent(s['markup'])}%"
        if s["markup"] is not None
        else f"سود: عمومی ({helpers.markup_to_percent(current_markup)}%)"
    )

    text = (
        f"📦 *{product['name']}*\n"
        f"قیمت خرید (irMarket): {helpers.fmt_usd(product['price_usd'])}\n"
        f"قیمت فروش به مشتری: {helpers.fmt_usd(retail)}\n"
        f"{markup_line}\n"
        f"دسته‌بندی: {category_name(s['category_id'])}\n"
        f"وضعیت: {'✅ فعال' if s['enabled'] else '🚫 غیرفعال'}"
    )
    toggle_label = "🚫 غیرفعال کن" if s["enabled"] else "✅ فعال کن"
    rows = [
        [InlineKeyboardButton(toggle_label, callback_data=f"adminprod:toggle:{product_id}")],
        [InlineKeyboardButton("🗂 تغییر دسته‌بندی", callback_data=f"adminprod:setcat:{product_id}")],
        [InlineKeyboardButton("📈 تغییر سود اختصاصی", callback_data=f"adminprod:setmarkup:{product_id}")],
        [InlineKeyboardButton("⬅️ لیست محصولات", callback_data="adminprod:list:0")],
    ]
    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows)
    )


async def show_admin_stats(query):
    user_count = await db.count_users()
    sales = await db.total_sales_usd()
    try:
        balance = await client.get_balance()
        balance_line = f"موجودی حساب irMarket: {helpers.fmt_usd(balance.get('balance_usd', balance.get('balance', 0)))}\n"
    except IrMarketError:
        balance_line = ""
    text = (
        f"📊 آمار\n\n"
        f"تعداد کاربران: {user_count}\n"
        f"مجموع فروش (تحویل‌شده): {helpers.fmt_usd(sales)}\n"
        f"{balance_line}"
    )
    await query.edit_message_text(text, reply_markup=back_kb("menu:admin"))


async def show_admin_recent_orders(query):
    orders = await db.recent_orders(15)
    if not orders:
        await query.edit_message_text("سفارشی ثبت نشده.", reply_markup=back_kb("menu:admin"))
        return
    lines = [
        f"#{o['id']} u:{o['telegram_id']} {o['product_name']} x{o['quantity']} "
        f"— {helpers.status_fa(o['status'])}"
        for o in orders
    ]
    await query.edit_message_text("🧾 سفارش‌های اخیر:\n" + "\n".join(lines), reply_markup=back_kb("menu:admin"))


# ------------------------------------------------------- free-text input ----
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    action = context.user_data.get("pending_action")
    if not action or not is_admin(telegram_id):
        return  # ignore stray text

    text = update.message.text.strip()

    if action == "admin_credit":
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit():
            await update.message.reply_text("فرمت اشتباهه. مثال: `123456789 25`", parse_mode=ParseMode.MARKDOWN)
            return
        target_id, amount_str = int(parts[0]), parts[1]
        try:
            amount = float(amount_str)
        except ValueError:
            await update.message.reply_text("مبلغ نامعتبره.")
            return
        target_user = await db.get_user(target_id)
        if not target_user:
            await update.message.reply_text("این کاربر هنوز با ربات /start نزده.")
            return
        new_balance = await db.adjust_wallet(target_id, amount)
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(
            f"✅ موجودی کاربر {target_id} به {helpers.fmt_usd(new_balance)} تغییر کرد."
        )
        try:
            await context.bot.send_message(
                target_id, f"💰 کیف پول شما {helpers.fmt_usd(amount)} شارژ شد."
            )
        except Exception:
            pass

    elif action == "admin_set_markup":
        try:
            percent = float(text.replace("%", "").strip())
        except ValueError:
            await update.message.reply_text("لطفاً فقط عدد بفرست، مثلا 30")
            return
        if percent < 0 or percent > 1000:
            await update.message.reply_text("عدد باید بین 0 تا 1000 باشه.")
            return
        new_markup = helpers.percent_to_markup(percent)
        await set_markup(new_markup)
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(
            f"✅ درصد سود به {percent}% تغییر کرد.\n"
            f"از الان قیمت محصولات با ضریب {new_markup} محاسبه می‌شه."
        )

    elif action == "admin_product_category":
        product_id = context.user_data.get("pending_product_id")
        if not product_id:
            context.user_data.pop("pending_action", None)
            return
        if text in ("-", "حذف", "بدون دسته"):
            await db.set_product_category(product_id, None)
            _update_product_cache(product_id, category_id=None)
            reply = "✅ دسته‌بندی این محصول برداشته شد."
        else:
            if len(text) > 20:
                await update.message.reply_text("اسم دسته‌بندی باید حداکثر ۲۰ کاراکتر باشه.")
                return
            category_id = await db.get_or_create_category(text)
            _categories_cache[category_id] = text
            await db.set_product_category(product_id, category_id)
            _update_product_cache(product_id, category_id=category_id)
            reply = f"✅ دسته‌بندی این محصول شد: {text}"
        context.user_data.pop("pending_action", None)
        context.user_data.pop("pending_product_id", None)
        await update.message.reply_text(reply)

    elif action == "admin_product_markup":
        product_id = context.user_data.get("pending_product_id")
        if not product_id:
            context.user_data.pop("pending_action", None)
            return
        if text in ("-", "پیش‌فرض", "حذف"):
            await db.set_product_markup(product_id, None)
            _update_product_cache(product_id, markup=None)
            reply = "✅ این محصول دوباره از سود عمومی استفاده می‌کنه."
        else:
            try:
                percent = float(text.replace("%", "").strip())
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد بفرست، مثلا 40")
                return
            if percent < 0 or percent > 1000:
                await update.message.reply_text("عدد باید بین 0 تا 1000 باشه.")
                return
            new_markup = helpers.percent_to_markup(percent)
            await db.set_product_markup(product_id, new_markup)
            _update_product_cache(product_id, markup=new_markup)
            reply = f"✅ سود اختصاصی این محصول به {percent}% تنظیم شد."
        context.user_data.pop("pending_action", None)
        context.user_data.pop("pending_product_id", None)
        await update.message.reply_text(reply)

    elif action == "admin_setwebhook":
        if not text.startswith("https://"):
            await update.message.reply_text("آدرس باید با https:// شروع بشه.")
            return
        full_url = text.rstrip("/") + config.WEBHOOK_PATH
        try:
            result = await client.register_webhook(full_url)
        except IrMarketError as e:
            await update.message.reply_text(f"خطا: {e.message}")
            return
        secret = result.get("secret")
        if secret:
            await db.set_setting("webhook_secret", secret)
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(
            "✅ Webhook ثبت شد: " + full_url + "\nاز این به بعد آپدیت سفارش‌ها خودکار دریافت می‌شه."
        )


def build_application() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("products", lambda u, c: on_callback_shim(u, c, "menu:products")))
    app.add_handler(CommandHandler("wallet", lambda u, c: on_callback_shim(u, c, "menu:wallet")))
    app.add_handler(CommandHandler("orders", lambda u, c: on_callback_shim(u, c, "menu:orders")))
    app.add_handler(CommandHandler("admin", lambda u, c: on_callback_shim(u, c, "menu:admin")))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


async def on_callback_shim(update: Update, context: ContextTypes.DEFAULT_TYPE, target: str):
    """Lets plain /commands reuse the callback-based menu code."""
    telegram_id = update.effective_user.id
    await db.get_or_create_user(telegram_id, update.effective_user.username)
    if target == "menu:admin" and not is_admin(telegram_id):
        await update.message.reply_text("این بخش فقط برای ادمین‌هاست.")
        return
    msg = await update.message.reply_text("...")

    class _FakeQuery:
        from_user = update.effective_user

        async def edit_message_text(self, *a, **kw):
            await msg.edit_text(*a, **kw)

    fq = _FakeQuery()
    if target == "menu:products":
        await show_categories(fq)
    elif target == "menu:wallet":
        await show_wallet(fq, telegram_id)
    elif target == "menu:orders":
        await show_orders(fq, telegram_id)
    elif target == "menu:admin":
        await show_admin_menu(fq)
