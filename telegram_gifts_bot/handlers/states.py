from aiogram.fsm.state import State, StatesGroup


class AccessStates(StatesGroup):
    waiting_target = State()


class FilterStates(StatesGroup):
    waiting_price = State()
    waiting_currency = State()
    waiting_categories = State()
