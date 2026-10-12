from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.keyboards import CATALOG, SELECTED, START, STOP, menu, selected_gifts_keyboard
from handlers.states import AccessStates


def build_router(config, db, scanner):
    router = Router(name="gift_tracker")

    async def snapshot(user_id: int):
        active, _, filters = await db.snapshot(config.admin_id)
        return active, filters

    async def context(message: Message):
        active, filters = await snapshot(message.from_user.id)
        admin = message.from_user.id == config.admin_id
        return admin, message.from_user.id == active, active, filters

    async def deny(message: Message):
        await message.answer("⛔ У вас нет доступа к этому боту.")

    def reply_menu(message: Message, filters):
        return menu(owner=message.from_user.id == config.admin_id, active=True,
                    parsing_enabled=bool(filters and filters.parsing_enabled))

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        admin, active, _, filters = await context(message)
        if not admin and not active:
            await deny(message)
            return
        if admin and active:
            text = "👑 Вы владелец и активный пользователь."
        elif active:
            text = "✅ Доступ активирован."
        else:
            text = "👑 Вы владелец. Сейчас активен другой пользователь."
        await message.answer(text, reply_markup=menu(
            owner=admin, active=active,
            parsing_enabled=bool(filters and filters.parsing_enabled)))

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Отменено.")

    @router.message(F.text == "🔑 Выдать доступ")
    async def grant_prompt(message: Message, state: FSMContext):
        if message.from_user.id != config.admin_id:
            await deny(message)
            return
        await state.set_state(AccessStates.waiting_target)
        await message.answer("Введите числовой Telegram ID пользователя. /cancel — отмена")

    @router.message(AccessStates.waiting_target)
    async def grant_value(message: Message, state: FSMContext):
        if message.from_user.id != config.admin_id:
            await state.clear()
            await deny(message)
            return
        try:
            user_id = int((message.text or "").strip())
            if not 0 < user_id < 2**63:
                raise ValueError
        except (ValueError, AttributeError):
            await message.answer("Нужен положительный числовой Telegram ID.")
            return
        await db.set_allowed_user(user_id)
        await state.clear()
        await message.answer(f"✅ Доступ выдан пользователю <code>{user_id}</code>.",
                             reply_markup=menu(owner=True, active=False))

    @router.message(F.text == "❌ Забрать доступ")
    async def revoke(message: Message, state: FSMContext):
        if message.from_user.id != config.admin_id:
            await deny(message)
            return
        await state.clear()
        await db.set_allowed_user(None)
        _, filters = await snapshot(config.admin_id)
        await message.answer("✅ Доступ забран. Активным снова стал владелец.",
                             reply_markup=menu(owner=True, active=True,
                                               parsing_enabled=bool(filters and filters.parsing_enabled)))

    @router.message(F.text == "📊 Статус доступа")
    async def access_status(message: Message):
        if message.from_user.id != config.admin_id:
            await deny(message)
            return
        allowed, revision = await db.access_status(config.admin_id)
        current = allowed or config.admin_id
        label = "владелец (по умолчанию)" if allowed is None else f"<code>{allowed}</code>"
        await message.answer(f"Активный пользователь: {label}\nРевизия: <code>{revision}</code>\n"
                             f"Сканер отправляет алерты только ID <code>{current}</code>.")

    async def show_selection(message: Message):
        _, active, _, filters = await context(message)
        if not active:
            await deny(message)
            return
        items = scanner.catalog.items
        if not items:
            await message.answer("⏳ Каталог ещё загружается, попробуйте через несколько секунд.")
            return
        selected = set(filters.selected_categories if filters else [])
        await message.answer("🎁 Выберите подарки для мониторинга. Снять весь выбор — остановить мониторинг категорий.",
                             reply_markup=selected_gifts_keyboard(items, selected))

    @router.message(F.text == CATALOG)
    async def catalog(message: Message):
        _, active, _, _ = await context(message)
        if not active:
            await deny(message)
            return
        items = scanner.catalog.items
        if not items:
            await message.answer("⏳ Каталог ещё загружается, попробуйте через несколько секунд.")
            return
        lines = ["📦 Каталог подарков"]
        lines.extend(f"{gift_id} — {html.quote(title)}" for gift_id, title in items.items())
        chunk = []
        chunk_length = 0
        for line in lines:
            if chunk and chunk_length + len(line) + 1 > 3900:
                await message.answer("\n".join(chunk))
                chunk = []
                chunk_length = 0
            chunk.append(line)
            chunk_length += len(line) + 1
        if chunk:
            await message.answer("\n".join(chunk))

    @router.message(F.text == SELECTED)
    async def selected(message: Message):
        await show_selection(message)

    async def callback_allowed(callback: CallbackQuery) -> bool:
        active, _ = await snapshot(callback.from_user.id)
        if callback.from_user.id != active:
            await callback.answer("Нет доступа", show_alert=True)
            return False
        return True

    @router.callback_query(F.data.startswith("gift_page:"))
    async def select_page(callback: CallbackQuery):
        if not await callback_allowed(callback):
            return
        page_value = callback.data.split(":", 1)[1]
        if page_value == "no-op":
            await callback.answer()
            return
        _, filters = await snapshot(callback.from_user.id)
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=selected_gifts_keyboard(
            scanner.catalog.items, set(filters.selected_categories), int(page_value)))

    @router.callback_query(F.data.startswith("gift_select:"))
    async def select_gift(callback: CallbackQuery):
        if not await callback_allowed(callback):
            return
        _, filters = await snapshot(callback.from_user.id)
        parts = callback.data.split(":")
        gift_id = parts[1]
        page_value = parts[2] if len(parts) > 2 else "0"
        selected = set(filters.selected_categories)
        if gift_id in selected:
            selected.remove(gift_id)
        else:
            selected.add(gift_id)
        await db.update_filters(callback.from_user.id, selected_categories=sorted(selected))
        await callback.answer("Выбор обновлён")
        await callback.message.edit_reply_markup(
            reply_markup=selected_gifts_keyboard(scanner.catalog.items, selected, int(page_value)))

    @router.callback_query(F.data.startswith("gift_all:"))
    async def select_all(callback: CallbackQuery):
        if not await callback_allowed(callback):
            return
        selected = [str(gift_id) for gift_id in scanner.catalog.items]
        await db.update_filters(callback.from_user.id, selected_categories=selected)
        await callback.answer("Выбраны все подарки")
        await callback.message.edit_reply_markup(
            reply_markup=selected_gifts_keyboard(scanner.catalog.items, set(selected),
                                                 int(callback.data.split(":", 1)[1])))

    @router.callback_query(F.data.startswith("gift_none:"))
    async def select_none(callback: CallbackQuery):
        if not await callback_allowed(callback):
            return
        await db.update_filters(callback.from_user.id, selected_categories=[])
        await callback.answer("Выбор снят: категории не мониторятся")
        await callback.message.edit_reply_markup(
            reply_markup=selected_gifts_keyboard(scanner.catalog.items, set(),
                                                 int(callback.data.split(":", 1)[1])))

    @router.callback_query(F.data == "gift_done")
    async def selection_done(callback: CallbackQuery):
        if not await callback_allowed(callback):
            return
        await callback.answer("Сохранено")
        await callback.message.edit_text("✅ Выбор подарков сохранён.")

    @router.message(F.text == START)
    async def start_parsing(message: Message):
        _, active, _, _ = await context(message)
        if not active:
            await deny(message)
            return
        _, current_filters = await snapshot(message.from_user.id)
        if not current_filters.selected_categories:
            await message.answer("Сначала выберите подарки в разделе «🎁 Выбранные подарки».")
            return
        await db.update_filters(message.from_user.id, parsing_enabled=True)
        _, filters = await snapshot(message.from_user.id)
        await message.answer("▶️ Парсинг запущен.", reply_markup=reply_menu(message, filters))

    @router.message(F.text == STOP)
    async def stop_parsing(message: Message):
        _, active, _, _ = await context(message)
        if not active:
            await deny(message)
            return
        await db.update_filters(message.from_user.id, parsing_enabled=False)
        _, filters = await snapshot(message.from_user.id)
        await message.answer("⏹ Парсинг остановлен.", reply_markup=reply_menu(message, filters))

    @router.message()
    async def fallback(message: Message):
        admin, active, _, _ = await context(message)
        if not admin and not active:
            await deny(message)
        else:
            await message.answer("Выберите действие кнопкой меню.")

    return router
