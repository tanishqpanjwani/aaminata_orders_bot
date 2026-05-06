"""
Aaminata Order Tracker Bot
- Natural language order parsing (regex, no API needed)
- Daily 8pm IST summary
- Full customer/order management via menus
"""

import logging
import csv
import io
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from database import Database
from config import BOT_TOKEN, ADMIN_IDS, SUMMARY_HOUR, SUMMARY_MINUTE

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database()

# ── Conversation states ───────────────────────────────────────────────────────
(
    CUST_NAME, CUST_PHONE, CUST_ADDRESS, CUST_NOTES,
    ORD_CUSTOMER, ORD_ITEMS, ORD_TOTAL, ORD_NOTES,
    STATUS_ORDER_ID, STATUS_NEW,
) = range(10)

STATUS_EMOJI = {
    "Pending": "🕐", "Processing": "⚙️",
    "Shipped": "🚚", "Delivered": "✅", "Cancelled": "❌"
}


# ── Auth ──────────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


# ── Regex-based order parser ──────────────────────────────────────────────────

# Hindi/Hinglish number words → digits
_HINDI_NUMS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5,
    "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, replace Hindi number words with digits."""
    text = text.lower().strip()
    text = re.sub(r"[₹,।]", " ", text)
    text = re.sub(r"\brs\.?\b", " ", text)
    for word, digit in _HINDI_NUMS.items():
        text = re.sub(rf"\b{word}\b", str(digit), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def parse_order_regex(text: str) -> dict | None:
    """
    Parse a free-form order message using regex.

    Expected loose format (any order, all combos):
      <product words> [<qty> [bottles|pcs|...]] <price> <customer name>

    Returns dict: {items, total, customer, confidence} or None if no price found.
    """
    norm = _normalise(text)

    # ── 1. Extract price (required) ───────────────────────────────────────────
    # Match a standalone number that looks like a price (≥ 2 digits or ends the string)
    price_pattern = re.compile(r"\b(\d{2,6}(?:\.\d{1,2})?)\b")
    price_matches = price_pattern.findall(norm)
    if not price_matches:
        return None

    # Heuristic: the price is the largest number that's ≥ 50
    # (avoids mistaking qty "3" for the price)
    candidates = [float(p) for p in price_matches if float(p) >= 50]
    if not candidates:
        return None
    total = max(candidates)
    # Remove the price token from the string for further parsing
    norm_no_price = re.sub(rf"\b{int(total) if total == int(total) else total}\b", " ", norm, count=1)
    norm_no_price = re.sub(r"\s+", " ", norm_no_price).strip()

    # ── 2. Extract quantity ───────────────────────────────────────────────────
    qty = 1
    qty_pattern = re.compile(
        r"\b(\d+)\s*(?:bottle[s]?|pcs?|piece[s]?|unit[s]?|packet[s]?|pack[s]?|no\.?|x)?\b",
        re.IGNORECASE,
    )
    qty_match = qty_pattern.search(norm_no_price)
    qty_str = ""
    if qty_match and int(qty_match.group(1)) < 100:  # sanity check
        qty = int(qty_match.group(1))
        qty_str = qty_match.group(0).strip()
        norm_no_price = norm_no_price.replace(qty_str, " ", 1)
        norm_no_price = re.sub(r"\s+", " ", norm_no_price).strip()

    # Strip trailing unit words
    norm_no_price = re.sub(
        r"\b(bottle[s]?|pcs?|piece[s]?|unit[s]?|packet[s]?|pack[s]?|ka order|ka|ne|ji)\b",
        " ", norm_no_price, flags=re.IGNORECASE,
    )
    norm_no_price = re.sub(r"\s+", " ", norm_no_price).strip()

    # ── 3. Split remaining into product + customer ────────────────────────────
    # Customer name is almost always at the END (last 1–2 words)
    # Product name is the beginning
    tokens = norm_no_price.split()

    if len(tokens) == 0:
        return None
    elif len(tokens) == 1:
        # Only one token — likely the product, no customer name
        product = tokens[0]
        customer = None
    else:
        # Last 1 or 2 tokens = customer name
        # Heuristic: if last token is short (<= 3 chars) it might be an abbreviation — take 2
        if len(tokens[-1]) <= 3 and len(tokens) >= 3:
            customer = " ".join(tokens[-2:]).title()
            product = " ".join(tokens[:-2])
        else:
            customer = tokens[-1].title()
            product = " ".join(tokens[:-1])

    if not product:
        return None

    # Format items string
    product_title = product.title()
    items = f"{product_title} x{qty}" if qty > 1 else product_title

    return {
        "items": items,
        "total": total,
        "customer": customer,
        "confidence": "high" if customer else "low",
    }


# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Not authorised.")
        return

    keyboard = [
        [InlineKeyboardButton("👤 Customers", callback_data="menu_customers"),
         InlineKeyboardButton("📦 Orders", callback_data="menu_orders")],
        [InlineKeyboardButton("📊 Export Data", callback_data="menu_export"),
         InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")],
    ]
    await update.message.reply_text(
        "🌿 *Aaminata Order Tracker*\n\n"
        "Just *type an order* anytime, like:\n"
        "`kumkumadi oil 3 bottles 3600 ansh`\n\n"
        "Or use the menu for everything else 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Natural language order handler ────────────────────────────────────────────
async def handle_natural_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called for any free text that isn't caught by a conversation handler."""
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("in_conversation"):
        return
    if context.user_data.get("awaiting_cust_search") or context.user_data.get("awaiting_order_view"):
        return

    text = update.message.text.strip()
    if len(text) < 5:
        return

    thinking = await update.message.reply_text("🤔 Reading your order...")

    parsed = parse_order_regex(text)

    if not parsed or not parsed.get("items") or parsed.get("total") is None:
        await thinking.edit_text(
            "❓ Couldn't read that as an order.\n\n"
            "Try: `kumkumadi oil 3 bottles 3600 ansh`\n"
            "Or use /start → 📦 Orders → ➕ New Order",
            parse_mode="Markdown",
        )
        return

    # Try to resolve the customer
    customer = None
    customer_line = ""
    if parsed.get("customer"):
        results = db.search_customers(parsed["customer"])
        if results:
            customer = results[0]
            customer_line = f"Customer: *{customer['name']}* ✅"
        else:
            customer_line = f"Customer: *{parsed['customer']}* ⚠️ _not in database yet_"

    context.user_data["pending_order"] = parsed
    context.user_data["pending_order"]["resolved_customer"] = customer
    context.user_data["original_text"] = text

    low_conf = parsed.get("confidence") != "high"
    conf_note = "\n⚠️ _Please double-check — I wasn't fully sure_" if low_conf else ""

    keyboard = [[
        InlineKeyboardButton("✅ Log it", callback_data="nlp_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="nlp_cancel"),
    ]]
    if not customer:
        keyboard.insert(0, [InlineKeyboardButton("➕ Add Customer First", callback_data="cust_add")])

    await thinking.edit_text(
        f"📦 *Order detected:*\n\n"
        f"Items: *{parsed['items']}*\n"
        f"Total: *₹{parsed['total']:.0f}*\n"
        f"{customer_line}"
        f"{conf_note}\n\n"
        f"Confirm?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def nlp_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pending = context.user_data.get("pending_order")
    if not pending:
        await query.edit_message_text("⚠️ No pending order. Please try again.")
        return

    customer = pending.get("resolved_customer")
    if not customer:
        await query.edit_message_text(
            f"⚠️ *{pending.get('customer', 'Customer')}* isn't in the database yet.\n\n"
            "Add them via /start → 👤 Customers → ➕ Add Customer, then resend the order.",
            parse_mode="Markdown",
        )
        context.user_data.pop("pending_order", None)
        return

    oid = db.add_order(
        customer_id=customer["id"],
        items=pending["items"],
        total=float(pending["total"]),
        notes=f"Quick order: {context.user_data.get('original_text', '')}",
    )
    context.user_data.pop("pending_order", None)
    context.user_data.pop("original_text", None)

    await query.edit_message_text(
        f"✅ *Order #{oid} logged!*\n\n"
        f"Customer: {customer['name']}\n"
        f"Items: {pending['items']}\n"
        f"Total: ₹{pending['total']:.0f}\n"
        f"Status: `Pending`",
        parse_mode="Markdown",
    )


async def nlp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pending_order", None)
    context.user_data.pop("original_text", None)
    await query.edit_message_text("❌ Order cancelled.")


# ── Daily summary ─────────────────────────────────────────────────────────────
async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    orders_today = db.get_todays_orders()
    all_orders = db.get_all_orders(limit=10000)

    revenue_today = sum(o["total"] for o in orders_today)
    revenue_total = sum(o["total"] for o in all_orders)

    status_counts: dict[str, int] = {}
    for o in orders_today:
        status_counts[o["status"]] = status_counts.get(o["status"], 0) + 1

    if orders_today:
        lines = [
            f"  {STATUS_EMOJI.get(o['status'], '❓')} `#{o['id']}` {o['customer_name']} — ₹{o['total']:.0f} — {o['items'][:25]}"
            for o in orders_today[:10]
        ]
        if len(orders_today) > 10:
            lines.append(f"  _...and {len(orders_today) - 10} more_")
        orders_text = "\n".join(lines)
    else:
        orders_text = "  _No orders today_"

    status_text = "  " + "  ".join(
        f"{STATUS_EMOJI.get(s, '❓')} {s}: {c}" for s, c in status_counts.items()
    ) if status_counts else "  —"

    message = (
        f"🌿 *Aaminata Daily Summary*\n"
        f"_{datetime.now().strftime('%d %B %Y')}_\n\n"
        f"📦 Orders today: *{len(orders_today)}*\n"
        f"💰 Revenue today: *₹{revenue_today:.0f}*\n\n"
        f"*Status breakdown:*\n{status_text}\n\n"
        f"*Today's orders:*\n{orders_text}\n\n"
        f"📊 All-time: *₹{revenue_total:.0f}* across *{len(all_orders)}* orders"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=message, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Summary send failed for {admin_id}: {e}")


# ── Main menu callbacks ───────────────────────────────────────────────────────
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_customers":
        keyboard = [
            [InlineKeyboardButton("➕ Add Customer", callback_data="cust_add")],
            [InlineKeyboardButton("📋 View All", callback_data="cust_list")],
            [InlineKeyboardButton("🔎 Search", callback_data="cust_search")],
            [InlineKeyboardButton("◀️ Back", callback_data="back_main")],
        ]
        await query.edit_message_text("👤 *Customers*", parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_orders":
        keyboard = [
            [InlineKeyboardButton("➕ New Order", callback_data="ord_add")],
            [InlineKeyboardButton("📋 All Orders", callback_data="ord_list")],
            [InlineKeyboardButton("🔄 Update Status", callback_data="ord_status")],
            [InlineKeyboardButton("🔍 Find by ID", callback_data="ord_view")],
            [InlineKeyboardButton("◀️ Back", callback_data="back_main")],
        ]
        await query.edit_message_text("📦 *Orders*", parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_export":
        await handle_export(update, context)

    elif data == "menu_help":
        await query.edit_message_text(
            "ℹ️ *Help*\n\n"
            "*Quick order — just type it:*\n"
            "`kumkumadi oil 3 bottles 3600 ansh`\n"
            "`neem toner 2 priya 800`\n"
            "`rose cream ek bottle 450 sunita ji`\n"
            "`3 bottle kumkumadi ansh ka order 3600 rs`\n\n"
            "The bot understands English, Hindi & Hinglish 🙂\n\n"
            "*Order statuses:*\n"
            "🕐 Pending → ⚙️ Processing → 🚚 Shipped → ✅ Delivered\n\n"
            "/cancel — cancel current action",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="back_main")]]),
        )

    elif data == "back_main":
        keyboard = [
            [InlineKeyboardButton("👤 Customers", callback_data="menu_customers"),
             InlineKeyboardButton("📦 Orders", callback_data="menu_orders")],
            [InlineKeyboardButton("📊 Export Data", callback_data="menu_export"),
             InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")],
        ]
        await query.edit_message_text(
            "🌿 *Aaminata Order Tracker*\n\nWhat would you like to do?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ── Customer flow ─────────────────────────────────────────────────────────────
async def cust_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["in_conversation"] = True
    await query.edit_message_text("👤 *Add Customer*\n\nCustomer's full name?", parse_mode="Markdown")
    return CUST_NAME

async def cust_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"] = {"name": update.message.text.strip()}
    await update.message.reply_text("📱 Phone number? (/skip to skip):")
    return CUST_PHONE

async def cust_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"]["phone"] = update.message.text.strip()
    await update.message.reply_text("🏠 Address? (/skip to skip):")
    return CUST_ADDRESS

async def cust_skip_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"]["phone"] = None
    await update.message.reply_text("🏠 Address? (/skip to skip):")
    return CUST_ADDRESS

async def cust_get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"]["address"] = update.message.text.strip()
    await update.message.reply_text("📝 Notes? (/skip to skip):")
    return CUST_NOTES

async def cust_skip_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"]["address"] = None
    await update.message.reply_text("📝 Notes? (/skip to skip):")
    return CUST_NOTES

async def cust_get_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"]["notes"] = update.message.text.strip()
    return await _save_customer(update, context)

async def cust_skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust"]["notes"] = None
    return await _save_customer(update, context)

async def _save_customer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = context.user_data.pop("new_cust")
    context.user_data.pop("in_conversation", None)
    cid = db.add_customer(c["name"], c.get("phone"), c.get("address"), c.get("notes"))
    await update.message.reply_text(
        f"✅ *Customer saved!*\n\nID: `{cid}` — *{c['name']}*\nPhone: {c.get('phone') or '—'}\n\nUse /start to go back.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

async def cust_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    customers = db.get_all_customers()
    if not customers:
        await query.edit_message_text("No customers yet.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_customers")]]))
        return
    lines = [f"`{c['id']}` — *{c['name']}*  {c['phone'] or ''}" for c in customers[:20]]
    await query.edit_message_text("👤 *All Customers*\n\n" + "\n".join(lines), parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_customers")]]))


# ── Order flow (menu) ─────────────────────────────────────────────────────────
async def ord_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["in_conversation"] = True
    await query.edit_message_text("📦 *New Order*\n\nCustomer name or ID:", parse_mode="Markdown")
    return ORD_CUSTOMER

async def ord_get_customer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    customer = db.get_customer_by_id(int(text)) if text.isdigit() else None
    if not customer:
        results = db.search_customers(text)
        customer = results[0] if results else None
    if not customer:
        await update.message.reply_text("❌ Not found. Try again or /cancel:")
        return ORD_CUSTOMER
    context.user_data["new_ord"] = {"customer_id": customer["id"], "customer_name": customer["name"]}
    await update.message.reply_text(f"✅ *{customer['name']}*\n\nItems ordered:", parse_mode="Markdown")
    return ORD_ITEMS

async def ord_get_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_ord"]["items"] = update.message.text.strip()
    await update.message.reply_text("💰 Total amount (₹):")
    return ORD_TOTAL

async def ord_get_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("₹", "").replace(",", "").strip()
    try:
        context.user_data["new_ord"]["total"] = float(text)
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number:")
        return ORD_TOTAL
    await update.message.reply_text("📝 Notes? (COD, prepaid, etc.) or /skip:")
    return ORD_NOTES

async def ord_get_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_ord"]["notes"] = update.message.text.strip()
    return await _save_order(update, context)

async def ord_skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_ord"]["notes"] = None
    return await _save_order(update, context)

async def _save_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    o = context.user_data.pop("new_ord")
    context.user_data.pop("in_conversation", None)
    oid = db.add_order(o["customer_id"], o["items"], o["total"], o.get("notes"))
    await update.message.reply_text(
        f"✅ *Order #{oid} saved!*\n\nCustomer: {o['customer_name']}\nItems: {o['items']}\nTotal: ₹{o['total']:.0f}\nStatus: `Pending`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

async def ord_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    orders = db.get_all_orders(limit=15)
    if not orders:
        await query.edit_message_text("No orders yet.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_orders")]]))
        return
    lines = [f"`#{o['id']}` {STATUS_EMOJI.get(o['status'], '❓')} *{o['customer_name']}* — ₹{o['total']:.0f}" for o in orders]
    await query.edit_message_text("📦 *Recent Orders*\n\n" + "\n".join(lines), parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_orders")]]))


# ── Status update flow ────────────────────────────────────────────────────────
async def status_update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["in_conversation"] = True
    await query.edit_message_text("🔄 *Update Status*\n\nEnter Order ID:", parse_mode="Markdown")
    return STATUS_ORDER_ID

async def status_get_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lstrip("#")
    if not text.isdigit():
        await update.message.reply_text("❌ Enter a valid order number:")
        return STATUS_ORDER_ID
    order = db.get_order_by_id(int(text))
    if not order:
        await update.message.reply_text("❌ Not found. Try again or /cancel:")
        return STATUS_ORDER_ID
    context.user_data["update_order_id"] = order["id"]
    keyboard = [[InlineKeyboardButton(s, callback_data=f"setstatus_{s}")]
                for s in ["Pending", "Processing", "Shipped", "Delivered", "Cancelled"]]
    await update.message.reply_text(
        f"Order *#{order['id']}* — {order['customer_name']}\nCurrent: `{order['status']}`\n\nNew status:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return STATUS_NEW

async def status_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_status = query.data.replace("setstatus_", "")
    oid = context.user_data.pop("update_order_id")
    context.user_data.pop("in_conversation", None)
    db.update_order_status(oid, new_status)
    await query.edit_message_text(f"✅ Order *#{oid}* → `{new_status}`\n\nUse /start to continue.", parse_mode="Markdown")
    return ConversationHandler.END


# ── Export ────────────────────────────────────────────────────────────────────
async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("📊 Generating CSV...")
    orders = db.get_all_orders(limit=10000)
    if not orders:
        if query:
            await query.edit_message_text("No orders to export yet.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Order ID", "Customer", "Phone", "Items", "Total (₹)", "Status", "Notes", "Date"])
    for o in orders:
        writer.writerow([o["id"], o["customer_name"], o.get("customer_phone", ""),
                         o["items"], o["total"], o["status"], o.get("notes", ""), o["created_at"][:10]])
    output.seek(0)
    filename = f"aaminata_{datetime.now().strftime('%Y%m%d')}.csv"
    bio = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    bio.name = filename
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=bio,
        filename=filename,
        caption=f"📊 {len(orders)} orders — {datetime.now().strftime('%d %b %Y')}",
    )


# ── Ad-hoc text (search / view / NLP fallthrough) ────────────────────────────
async def cust_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔎 Type customer name to search:")
    context.user_data["awaiting_cust_search"] = True

async def ord_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Enter Order ID:")
    context.user_data["awaiting_order_view"] = True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("awaiting_cust_search"):
        context.user_data.pop("awaiting_cust_search")
        results = db.search_customers(update.message.text.strip())
        if not results:
            await update.message.reply_text("❌ No customers found. Use /start to try again.")
            return
        lines = []
        for c in results[:10]:
            orders = db.get_orders_by_customer(c["id"])
            lines.append(f"`{c['id']}` — *{c['name']}*\n   📱 {c['phone'] or '—'}  |  📦 {len(orders)} order(s)")
        await update.message.reply_text("🔎 *Results*\n\n" + "\n\n".join(lines), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_order_view"):
        context.user_data.pop("awaiting_order_view")
        text = update.message.text.strip().lstrip("#")
        if text.isdigit():
            order = db.get_order_by_id(int(text))
            if order:
                await update.message.reply_text(
                    f"📦 *Order #{order['id']}*\n\n"
                    f"Customer: *{order['customer_name']}*\n"
                    f"Phone: {order.get('customer_phone') or '—'}\n"
                    f"Items: {order['items']}\n"
                    f"Total: ₹{order['total']:.0f}\n"
                    f"Status: {STATUS_EMOJI.get(order['status'], '❓')} `{order['status']}`\n"
                    f"Date: {order['created_at'][:10]}",
                    parse_mode="Markdown",
                )
                return
        await update.message.reply_text("❌ Order not found. Use /start to try again.")
        return

    # Fall through to NLP
    await handle_natural_order(update, context)


# ── Cancel ────────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Use /start to go back.")
    return ConversationHandler.END


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    import datetime as dt
    from zoneinfo import ZoneInfo

    app = Application.builder().token(BOT_TOKEN).build()

    cust_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cust_add_start, pattern="^cust_add$")],
        states={
            CUST_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_get_name)],
            CUST_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_get_phone),
                           CommandHandler("skip", cust_skip_phone)],
            CUST_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_get_address),
                           CommandHandler("skip", cust_skip_address)],
            CUST_NOTES:   [MessageHandler(filters.TEXT & ~filters.COMMAND, cust_get_notes),
                           CommandHandler("skip", cust_skip_notes)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    ord_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ord_add_start, pattern="^ord_add$")],
        states={
            ORD_CUSTOMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_customer)],
            ORD_ITEMS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_items)],
            ORD_TOTAL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_total)],
            ORD_NOTES:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ord_get_notes),
                           CommandHandler("skip", ord_skip_notes)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    status_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(status_update_start, pattern="^ord_status$")],
        states={
            STATUS_ORDER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, status_get_order_id)],
            STATUS_NEW:      [CallbackQueryHandler(status_set, pattern="^setstatus_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(cust_conv)
    app.add_handler(ord_conv)
    app.add_handler(status_conv)
    app.add_handler(CallbackQueryHandler(cust_list,         pattern="^cust_list$"))
    app.add_handler(CallbackQueryHandler(cust_search_start, pattern="^cust_search$"))
    app.add_handler(CallbackQueryHandler(ord_list,          pattern="^ord_list$"))
    app.add_handler(CallbackQueryHandler(ord_view_start,    pattern="^ord_view$"))
    app.add_handler(CallbackQueryHandler(nlp_confirm,       pattern="^nlp_confirm$"))
    app.add_handler(CallbackQueryHandler(nlp_cancel,        pattern="^nlp_cancel$"))
    app.add_handler(CallbackQueryHandler(main_menu_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Daily summary at 8pm IST
    app.job_queue.run_daily(
        send_daily_summary,
        time=dt.time(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, tzinfo=ZoneInfo("Asia/Kolkata")),
        name="daily_summary",
    )

    logger.info(f"🌿 Aaminata bot running — summary at {SUMMARY_HOUR}:{SUMMARY_MINUTE:02d} IST")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
