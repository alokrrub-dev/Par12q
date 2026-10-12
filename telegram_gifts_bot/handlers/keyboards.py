from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


CATALOG = "📦 Каталог подарков"
SELECTED = "🎁 Выбранные подарки"
START = "▶️ Начать парсинг"
STOP = "⏹ Остановить"
# Backwards-compatible name for the superseded router module.
FILTERS = SELECTED


def menu(*, owner: bool, active: bool, parsing_enabled: bool = False,
         alerts_enabled: bool | None = None) -> ReplyKeyboardMarkup:
    # `alerts_enabled` is accepted for compatibility with the pre-selection router.
    if alerts_enabled is not None:
        parsing_enabled = alerts_enabled
    rows = []
    if active:
        rows.extend([[KeyboardButton(text=CATALOG)],
                     [KeyboardButton(text=START), KeyboardButton(text=STOP)],
                     [KeyboardButton(text=SELECTED)]])
    if owner:
        rows.extend([[KeyboardButton(text="🔑 Выдать доступ"), KeyboardButton(text="❌ Забрать доступ")],
                     [KeyboardButton(text="📊 Статус доступа")]])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def selected_gifts_keyboard(items: dict[int, str], selected: set[str], page: int = 0) -> InlineKeyboardMarkup:
    page_size = 20
    ordered = sorted(items.items(), key=lambda pair: str(pair[1]).lower())
    pages = max(1, (len(ordered) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    rows = []
    for gift_id, title in ordered[page * page_size:(page + 1) * page_size]:
        mark = "✅" if str(gift_id) in selected else "▫️"
        label = f"{mark} {str(title)} [{gift_id}]"[:60]
        rows.append([InlineKeyboardButton(text=label, callback_data=f"gift_select:{gift_id}:{page}")])
    rows.append([
        InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"gift_all:{page}"),
        InlineKeyboardButton(text="⬜ Снять все", callback_data=f"gift_none:{page}"),
    ])
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="◀️", callback_data=f"gift_page:{page - 1}"))
    navigation.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="gift_page:no-op"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton(text="▶️", callback_data=f"gift_page:{page + 1}"))
    rows.append(navigation)
    rows.append([InlineKeyboardButton(text="Готово", callback_data="gift_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
