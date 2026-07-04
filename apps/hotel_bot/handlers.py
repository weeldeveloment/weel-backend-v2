"""
Hotel Bot handlers.

Flow:
  /start  →  ask phone
  phone   →  send OTP via Eskiz (user must be registered in PMS as platform_user)
  OTP     →  verify → show organizations
  org cb  →  show upcoming bookings for that organization's properties
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.core.cache import cache

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.users.services import EskizService, OTPRedisService
from apps.users.models.logs import SmsPurpose
from apps.users.raw_repository import create_sms_log
from apps.platform.raw_repository import get_platform_user_by_phone, get_user_organizations
from apps.pms.repository import list_properties, list_bookings, list_newest_bookings

logger = logging.getLogger(__name__)

# Redis key prefix for bot session state
_STATE_PREFIX = "hotel_bot:state:"
_ORG_PREFIX = "hotel_bot:org:"
_USER_PREFIX = "hotel_bot:user:"
_STATE_TTL = 600  # 10 minutes

STATE_WAIT_PHONE = "wait_phone"
STATE_WAIT_OTP = "wait_otp"
STATE_AUTHED = "authed"


def _state_key(chat_id: int) -> str:
    return f"{_STATE_PREFIX}{chat_id}"


def _org_key(chat_id: int) -> str:
    return f"{_ORG_PREFIX}{chat_id}"


def _user_key(chat_id: int) -> str:
    return f"{_USER_PREFIX}{chat_id}"


def _get_state(chat_id: int) -> str | None:
    return cache.get(_state_key(chat_id))


def _set_state(chat_id: int, state: str) -> None:
    cache.set(_state_key(chat_id), state, _STATE_TTL)


def _clear_state(chat_id: int) -> None:
    cache.delete(_state_key(chat_id))
    cache.delete(_user_key(chat_id))


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    _clear_state(chat_id)
    _set_state(chat_id, STATE_WAIT_PHONE)
    keyboard = ReplyKeyboardMarkup(
        [["\U0000260E Telefon raqamni yuborish"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Assalomu alaykum! Hotel boshqaruv botiga xush kelibsiz.\n\n"
        "Davom etish uchun PMS tizimiga ro'yxatdan o'tgan telefon raqamingizni yuboring:\n"
        "Masalan: +998901234567",
        reply_markup=keyboard,
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    state = _get_state(chat_id)

    if state == STATE_WAIT_PHONE:
        await _handle_phone(update, chat_id, text)
    elif state == STATE_WAIT_OTP:
        await _handle_otp(update, chat_id, text)
    elif state == STATE_AUTHED:
        await _handle_authed_message(update, chat_id, text)
    else:
        _set_state(chat_id, STATE_WAIT_PHONE)
        await update.message.reply_text(
            "Iltimos, /start buyrug'ini yuboring."
        )


async def _handle_phone(update: Update, chat_id: int, phone: str) -> None:
    # Normalize: strip emoji/spaces, ensure starts with +
    phone = phone.replace(" ", "").replace("-", "")
    if phone.startswith("\U0000260E"):
        phone = phone[1:].strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"

    if len(phone) < 10:
        await update.message.reply_text(
            "Noto'g'ri format. Iltimos, to'liq telefon raqam kiriting.\nMasalan: +998901234567"
        )
        return

    # Check if phone is registered in PMS (platform_user table)
    user = get_platform_user_by_phone(phone)
    if not user:
        await update.message.reply_text(
            "Bu raqam PMS tizimida ro'yxatdan o'tmagan.\n"
            "Iltimos, PMS administratoriga murojaat qiling yoki boshqa raqam kiriting."
        )
        return

    # Send OTP via Eskiz with the standard WEEL verification template
    eskiz = EskizService()
    otp_code = OTPRedisService.create_otp(phone, SmsPurpose.PMS_LOGIN)

    try:
        eskiz.send_sms(phone, otp_code)
        is_sent = True
    except Exception:
        logger.exception("Failed to send OTP to %s", phone)
        is_sent = False

    create_sms_log(phone, SmsPurpose.PMS_LOGIN, is_sent)

    if not is_sent:
        await update.message.reply_text(
            "SMS yuborishda xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."
        )
        return

    # Store phone temporarily
    cache.set(_user_key(chat_id), {"phone": phone, "platform_user_id": user["id"]}, _STATE_TTL)
    _set_state(chat_id, STATE_WAIT_OTP)

    await update.message.reply_text(
        f"📱 {phone} raqamiga tasdiqlash kodi yuborildi.\n"
        "Iltimos, kodni kiriting:"
    )


async def _handle_otp(update: Update, chat_id: int, otp: str) -> None:
    user_data = cache.get(_user_key(chat_id))
    if not user_data:
        _set_state(chat_id, STATE_WAIT_PHONE)
        await update.message.reply_text("Sessiya tugadi. /start orqali qaytadan boshlang.")
        return

    phone = user_data["phone"]
    verified, _ = OTPRedisService.verify_otp(phone, otp, SmsPurpose.PMS_LOGIN)

    if not verified:
        await update.message.reply_text(
            "Noto'g'ri kod. Iltimos, qaytadan urinib ko'ring."
        )
        return
    _set_state(chat_id, STATE_AUTHED)

    platform_user_id = user_data["platform_user_id"]
    cache.set(_user_key(chat_id), {"phone": phone, "platform_user_id": platform_user_id}, 86400)

    await update.message.reply_text("✅ Muvaffaqiyatli tasdiqlandi!")
    await _show_organizations(update, chat_id, platform_user_id)


async def _show_organizations(update: Update, chat_id: int, platform_user_id: int) -> None:
    orgs = get_user_organizations(platform_user_id)
    if not orgs:
        await update.message.reply_text(
            "Sizga biriktirilgan hotel topilmadi.\n"
            "Iltimos, PMS administratoriga murojaat qiling."
        )
        return

    if len(orgs) == 1:
        await _show_bookings_for_org(update, orgs[0])
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            text=f"🏨 {org['name']}",
            callback_data=f"org:{org['id']}"
        )]
        for org in orgs
    ])
    await update.message.reply_text(
        "Qaysi hotelni ko'rmoqchisiz?",
        reply_markup=keyboard,
    )


async def _show_bookings_for_org(update_or_query, org: dict) -> None:
    org_id = org["id"]
    properties = list_properties(organization_id=org_id)

    if not properties:
        text = f"🏨 *{org['name']}*\n\nBu hotelda xona topilmadi."
        if hasattr(update_or_query, "message"):
            await update_or_query.message.reply_text(text, parse_mode="Markdown")
        else:
            await update_or_query.edit_message_text(text, parse_mode="Markdown")
        return

    today = date.today()
    all_lines = [f"🏨 *{org['name']}* — Yaqin 30 kundagi zayafkalar\n"]

    for prop in properties:
        bookings = list_bookings(
            property_id=prop["id"],
            from_date=today,
            to_date=today + timedelta(days=30),
        )
        if not bookings:
            continue

        all_lines.append(f"\n📍 *{prop['name']}*")
        for b in bookings[:10]:
            guest = f"{b.get('guest_first_name') or ''} {b.get('guest_last_name') or ''}".strip() or "Noma'lum mehmon"
            check_in = b.get("check_in") or ""
            check_out = b.get("check_out") or ""
            room = b.get("room_number") or "—"
            status = b.get("status") or "—"
            status_emoji = _status_emoji(status)
            booking_num = b.get("booking_number") or f"#{b.get('id')}"
            all_lines.append(
                f"  {status_emoji} *{booking_num}* — {guest}\n"
                f"     Xona: {room} | {check_in} → {check_out} | {status}"
            )

    if len(all_lines) == 1:
        all_lines.append("\nYaqin 30 kunda zayafka yo'q.")

    text = "\n".join(all_lines)
    # Telegram message limit is 4096 chars
    if len(text) > 4096:
        text = text[:4090] + "..."

    if hasattr(update_or_query, "message"):
        await update_or_query.message.reply_text(text, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(text, parse_mode="Markdown")


def _status_emoji(status: str) -> str:
    return {
        "new": "🆕",
        "confirmed": "✅",
        "checked_in": "🏠",
        "checked_out": "🏁",
        "cancelled": "❌",
        "no_show": "👻",
    }.get(status, "📋")


async def _handle_authed_message(update: Update, chat_id: int, text: str) -> None:
    user_data = cache.get(_user_key(chat_id))
    if not user_data:
        _clear_state(chat_id)
        await update.message.reply_text("Sessiya tugadi. /start orqali qaytadan kiring.")
        return

    await _show_organizations(update, chat_id, user_data["platform_user_id"])


async def new_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = _get_state(chat_id)

    if state != STATE_AUTHED:
        await update.message.reply_text(
            "Avval tizimga kiring. /start buyrug'ini yuboring."
        )
        return

    user_data = cache.get(_user_key(chat_id))
    if not user_data:
        _clear_state(chat_id)
        await update.message.reply_text("Sessiya tugadi. /start orqali qaytadan kiring.")
        return

    platform_user_id = user_data["platform_user_id"]
    orgs = get_user_organizations(platform_user_id)
    if not orgs:
        await update.message.reply_text("Sizga biriktirilgan hotel topilmadi.")
        return

    if len(orgs) == 1:
        await _show_newest_for_org(update, orgs[0])
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            text=f"🏨 {org['name']}",
            callback_data=f"new_org:{org['id']}"
        )]
        for org in orgs
    ])
    await update.message.reply_text(
        "Qaysi hotelning yangi zayafkalarini ko'rmoqchisiz?",
        reply_markup=keyboard,
    )


async def _show_newest_for_org(update_or_query, org: dict) -> None:
    org_id = org["id"]
    properties = list_properties(organization_id=org_id)

    if not properties:
        text = f"🏨 *{org['name']}*\n\nBu hotelda mulk topilmadi."
        if hasattr(update_or_query, "message"):
            await update_or_query.message.reply_text(text, parse_mode="Markdown")
        else:
            await update_or_query.edit_message_text(text, parse_mode="Markdown")
        return

    all_lines = [f"🏨 *{org['name']}* — Eng yangi 10 ta zayafka\n"]

    for prop in properties:
        bookings = list_newest_bookings(property_id=prop["id"], limit=10)
        if not bookings:
            continue

        all_lines.append(f"\n📍 *{prop['name']}*")
        for b in bookings:
            guest = f"{b.get('guest_first_name') or ''} {b.get('guest_last_name') or ''}".strip() or "Noma'lum mehmon"
            check_in = b.get("check_in") or ""
            check_out = b.get("check_out") or ""
            room = b.get("room_number") or "—"
            status = b.get("status") or "—"
            status_emoji = _status_emoji(status)
            booking_num = b.get("booking_number") or f"#{b.get('id')}"
            created = str(b.get("created_at") or "")[:16]
            all_lines.append(
                f"  {status_emoji} *{booking_num}* — {guest}\n"
                f"     Xona: {room} | {check_in} → {check_out} | {status}\n"
                f"     Yaratildi: {created}"
            )

    if len(all_lines) == 1:
        all_lines.append("\nZayafka yo'q.")

    text = "\n".join(all_lines)
    if len(text) > 4096:
        text = text[:4090] + "..."

    if hasattr(update_or_query, "message"):
        await update_or_query.message.reply_text(text, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(text, parse_mode="Markdown")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data or ""

    from apps.platform.raw_repository import get_organization_by_id

    if data.startswith("org:"):
        org_id = int(data.split(":")[1])
        org = get_organization_by_id(org_id)
        if not org:
            await query.edit_message_text("Hotel topilmadi.")
            return
        await _show_bookings_for_org(query, org)

    elif data.startswith("new_org:"):
        org_id = int(data.split(":")[1])
        org = get_organization_by_id(org_id)
        if not org:
            await query.edit_message_text("Hotel topilmadi.")
            return
        await _show_newest_for_org(query, org)
