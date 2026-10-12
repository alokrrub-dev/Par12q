import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select

from database.models import UserFilters
from handlers.keyboards import CATALOG, FILTERS, menu
from handlers.states import AccessStates, FilterStates

log = logging.getLogger(__name__)


def build_router(config, db):
    router = Router(name="gift_tracker")

    async def snapshot(user_id: int):
        active, _, filters = await db.snapshot(config.admin_id)
        return active, filters

    async def user_context(message: Message):
        active, filters = await snapshot(message.from_user.id)
        is_admin = message.from_user.id == config.admin_id
        return is_admin, message.from_user.id == active, active, filters

    async def deny(message: Message):
        await message.answer("⛔ У вас нет доступа к этому боту.")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        is_admin, active, active_id, filters = await user_context(message)
        if not is_admin and not active:
            await deny(message)
            return
        title = "👑 Вы владелец и активный пользователь." if is_admin and active else (
            "✅ Доступ активирован." if active else "👑 Вы владелец. Сейчас активен другой пользователь.")
        await message.answer(title, reply_markup=menu(
            owner=is_admin, active=active,
            alerts_enabled=filters.alerts_enabled if filters else True,
        ))

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Отменено.")

    @router.message(lambda message: message.text == "🔑 Выдать доступ")
    async def grant_prompt(message: Message, state: FSMContext):
        if message.from_user.id != config.admin_id:
            await deny(message); return
        await state.set_state(AccessStates.waiting_target)
        await message.answer("Введите числовой Telegram ID пользователя. /cancel — отмена")

    @router.message(AccessStates.waiting_target)
    async def grant_value(message: Message, state: FSMContext):
        if message.from_user.id != config.admin_id:
            await state.clear(); await deny(message); return
        try:
            user_id = int(message.text.strip())
            if not 0 < user_id < 2**63:
                raise ValueError
        except (ValueError, AttributeError):
            await message.answer("Нужен положительный числовой Telegram ID.")
            return
        await db.set_allowed_user(user_id)
        await state.clear()
        await message.answer(f"✅ Доступ выдан пользователю <code>{user_id}</code>.",
                             reply_markup=menu(owner=True, active=False))

    @router.message(lambda message: message.text == "❌ Забрать доступ")
    async def revoke(message: Message, state: FSMContext):
        if message.from_user.id != config.admin_id:
            await deny(message); return
        await state.clear()
        await db.set_allowed_user(None)
        _, filters = await snapshot(config.admin_id)
        await message.answer("✅ Доступ забран. Активным снова стал владелец.", reply_markup=menu(
            owner=True, active=True, alerts_enabled=filters.alerts_enabled if filters else True))

    @router.message(lambda message: message.text == "📊 Статус доступа")
    async def access_status(message: Message):
        if message.from_user.id != config.admin_id:
            await deny(message); return
        allowed, revision = await db.access_status(config.admin_id)
        current = allowed or config.admin_id
        label = "владелец (по умолчанию)" if allowed is None else f"<code>{allowed}</code>"
        await message.answer(f"Активный пользователь: {label}\nРевизия: <code>{revision}</code>\n"
                             f"Сканер отправляет алерты только ID <code>{current}</code>.")

    @router.message(lambda message: message.text == CATALOG)
    async def catalog(message: Message):
        is_admin, active, _, filters = await user_context(message)
        if not active:
            await deny(message); return
        categories = ", ".join(filters.selected_categories) if filters and filters.selected_categories else "все"
        max_price = filters.max_price if filters and filters.max_price is not None else "без лимита"
        currency = filters.price_currency if filters else "ANY"
        await message.answer("📦 <b>Мониторинг маркета включён</b>\n"
                             f"Категории: {categories}\nЛимит: {max_price} {currency}\n"
                             "Свежие уникальные лоты придут сюда автоматически.")

    @router.message(lambda message: message.text == FILTERS)
    async def filters_prompt(message: Message, state: FSMContext):
        _, active, _, filters = await user_context(message)
        if not active:
            await deny(message); return
        current = filters.max_price if filters and filters.max_price is not None else "нет"
        await state.set_state(FilterStates.waiting_price)
        await message.answer(f"Максимальная цена (сейчас: {current}). Введите число или /skip для снятия лимита.")

    @router.message(FilterStates.waiting_price)
    async def filter_price(message: Message, state: FSMContext):
        if message.from_user.id != (await snapshot(message.from_user.id))[0]:
            await state.clear(); await deny(message); return
        text = (message.text or "").strip().lower()
        if text == "/skip":
            await db.update_filters(message.from_user.id, max_price=None)
        else:
            try:
                value = Decimal(text.replace(",", "."))
                if value <= 0: raise InvalidOperation
            except (InvalidOperation, ValueError):
                await message.answer("Введите положительное число или /skip.")
                return
            await db.update_filters(message.from_user.id, max_price=value)
        await state.set_state(FilterStates.waiting_currency)
        await message.answer("Валюта: XTR, TON или ANY (любая).")

    @router.message(FilterStates.waiting_currency)
    async def filter_currency(message: Message, state: FSMContext):
        if message.from_user.id != (await snapshot(message.from_user.id))[0]:
            await state.clear(); await deny(message); return
        currency = (message.text or "").strip().upper()
        if currency not in {"ANY", "XTR", "TON"}:
            await message.answer("Введите ANY, XTR или TON.")
            return
        await db.update_filters(message.from_user.id, price_currency=currency)
        await state.set_state(FilterStates.waiting_categories)
        await message.answer("Категории через запятую (ID базовых подарков) или /skip для всех.")

    @router.message(FilterStates.waiting_categories)
    async def filter_categories(message: Message, state: FSMContext):
        if message.from_user.id != (await snapshot(message.from_user.id))[0]:
            await state.clear(); await deny(message); return
        text = (message.text or "").strip()
        if text == "/skip":
            categories = []
        else:
            categories = [part.strip() for part in text.split(",") if part.strip().isdigit()]
            if len(categories) != len([part for part in text.split(",") if part.strip()]):
                await message.answer("Используйте только числовые ID через запятую или /skip.")
                return
        await db.update_filters(message.from_user.id, selected_categories=categories)
        await state.clear()
        await message.answer("✅ Фильтры сохранены.")

    @router.message(lambda message: (message.text or "").startswith("🔔 Алерты:"))
    async def toggle_alerts(message: Message):
        _, active, _, filters = await user_context(message)
        if not active:
            await deny(message); return
        enabled = not bool(filters and filters.alerts_enabled)
        await db.update_filters(message.from_user.id, alerts_enabled=enabled)
        await message.answer(f"🔔 Алерты {'включены' if enabled else 'выключены'}.", reply_markup=menu(
            owner=message.from_user.id == config.admin_id, active=True, alerts_enabled=enabled))

    @router.message()
    async def fallback(message: Message):
        is_admin, active, _, _ = await user_context(message)
        if not is_admin and not active:
            await deny(message)
        else:
            await message.answer("Выберите действие кнопкой меню.")

    return router
