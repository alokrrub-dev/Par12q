import asyncio
import logging

from aiogram import Bot, html
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from services.cache import RecentIDs
from services.scanner import GiftEvent

log = logging.getLogger(__name__)


def seller_label(seller) -> str:
    if seller.username:
        return f"@{html.quote(seller.username)}"
    return html.quote(seller.name)


def contact_url(seller) -> str:
    return f"https://t.me/{seller.username}" if seller.username else f"tg://user?id={seller.user_id}"


def render(event: GiftEvent) -> tuple[str, InlineKeyboardMarkup]:
    gift, seller = event.gift, event.seller
    lines = [
        # Put the gift URL first so Telegram selects its preview.
        gift.url,
        f"🎁 <b>{html.quote(gift.title)}</b> №{gift.number}",
        f"Владелец: {seller_label(seller)}",
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Написать владельцу", url=contact_url(seller)),
        InlineKeyboardButton(text="🛒 Открыть подарок", url=gift.url),
    ]])
    return "\n".join(lines), markup


async def notifier_loop(config, db, bot: Bot, queue: asyncio.Queue[GiftEvent], stop: asyncio.Event):
    recent_owners = RecentIDs(2000)
    while not stop.is_set():
        try:
            event = await asyncio.wait_for(queue.get(), 1)
        except asyncio.TimeoutError:
            continue
        try:
            active, _, filters = await db.snapshot(config.admin_id)
            if (active != event.recipient_id or filters is None or not filters.parsing_enabled
                    or str(event.gift.category_id) not in filters.selected_categories):
                await db.finish(event.gift.id, "skipped")
                continue
            text, markup = render(event)
            owner_id = event.gift.owner_id
            if owner_id in recent_owners:
                await db.finish(event.gift.id, "duplicate_owner")
                continue
            if not await db.claim_owner(owner_id, event.gift.id, active):
                recent_owners.add(owner_id)
                await db.finish(event.gift.id, "duplicate_owner")
                continue
            recent_owners.add(owner_id)
            preview = LinkPreviewOptions(
                url=event.gift.url,
                prefer_large_media=True,
            )
            try:
                message = await bot.send_message(
                    active, text, reply_markup=markup, link_preview_options=preview,
                )
            except TelegramBadRequest as error:
                if "BUTTON_USER_INVALID" not in error.message.upper():
                    raise
                # This is an explicit rejection: the first message was not sent.
                # Keep the durable owner reservation and retry once with only
                # the gift button; never retry ambiguous delivery failures here.
                log.info("Invalid owner button for gift %s; sending with gift button only", event.gift.id)
                gift_markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🛒 Открыть подарок", url=event.gift.url),
                ]])
                message = await bot.send_message(
                    active, text, reply_markup=gift_markup, link_preview_options=preview,
                )
            await db.finish(event.gift.id, "sent", message.message_id)
        except TelegramAPIError as error:
            log.warning("Alert delivery failed for gift %s: %s", event.gift.id, error)
            await db.finish(event.gift.id, "failed")
        except Exception:
            log.exception("Unexpected alert delivery failure")
            await db.finish(event.gift.id, "failed")
        finally:
            queue.task_done()
