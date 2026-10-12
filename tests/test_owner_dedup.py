"""Isolated notifier tests: no Telegram credentials or database connection needed.

Loads the actual notifier loop with controlled external dependencies so these
checks can also run on a Python installation without the bot's packages.
"""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeliveryError(Exception):
    pass


class BadRequest(DeliveryError):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def load_notifier():
    cache_namespace = {}
    cache_path = ROOT / "telegram_gifts_bot/services/cache.py"
    exec(compile(cache_path.read_text(encoding="utf-8"), str(cache_path), "exec"), cache_namespace)
    path = ROOT / "telegram_gifts_bot/services/notifier.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    loop = next(node for node in source.body if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "notifier_loop")
    namespace = {
        "asyncio": asyncio, "Bot": object, "GiftEvent": object,
        "RecentIDs": cache_namespace["RecentIDs"],
        "TelegramAPIError": DeliveryError,
        "TelegramBadRequest": BadRequest,
        "InlineKeyboardButton": lambda **kwargs: SimpleNamespace(**kwargs),
        "InlineKeyboardMarkup": lambda **kwargs: SimpleNamespace(**kwargs),
        "LinkPreviewOptions": lambda **kwargs: kwargs,
        "render": lambda event: (str(event.gift.id), SimpleNamespace(inline_keyboard=[[
            SimpleNamespace(url=f"tg://user?id={event.gift.owner_id}"),
            SimpleNamespace(url=event.gift.url),
        ]])),
        "log": logging.getLogger("test_notifier"),
    }
    exec(compile(ast.Module(body=[loop], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["notifier_loop"]


notifier_loop = load_notifier()


class FakeDatabase:
    def __init__(self):
        self.owners = {}
        self.claim_attempts = 0
        self.finished = {}
        self.active = 1
        self.filters = SimpleNamespace(parsing_enabled=True, selected_categories=["100", "200"])
        self.lock = asyncio.Lock()

    async def snapshot(self, admin_id):
        return self.active, 0, self.filters

    async def claim_owner(self, owner_id, gift_id, recipient_id):
        async with self.lock:
            self.claim_attempts += 1
            if owner_id in self.owners:
                return False
            self.owners[owner_id] = (gift_id, recipient_id)
            return True

    async def finish(self, gift_id, status, message_id=None):
        self.finished[gift_id] = status


class FakeBot:
    def __init__(self, fail=False, errors=None):
        self.sent = []
        self.options = []
        self.fail = fail
        self.errors = list(errors or [])

    async def send_message(self, recipient, text, **kwargs):
        self.sent.append((recipient, text))
        self.options.append(kwargs)
        await asyncio.sleep(0)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        if self.fail:
            raise DeliveryError("Connection interrupted")
        return SimpleNamespace(message_id=len(self.sent))


def event(gift_id, owner_id, category_id=100, recipient_id=1):
    return SimpleNamespace(gift=SimpleNamespace(id=gift_id, owner_id=owner_id,
                           category_id=category_id, url=f"https://t.me/nft/Test-{gift_id}"),
                           recipient_id=recipient_id)


async def deliver(db, bot, events):
    queue = asyncio.Queue()
    for entry in events:
        queue.put_nowait(entry)
    stop = asyncio.Event()
    task = asyncio.create_task(notifier_loop(SimpleNamespace(admin_id=1), db, bot, queue, stop))
    try:
        await asyncio.wait_for(queue.join(), 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class OwnerDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_owner_button_falls_back_to_gift_only(self):
        db = FakeDatabase()
        bot = FakeBot(errors=[BadRequest("Bad Request: BUTTON_USER_INVALID")])
        await deliver(db, bot, [event(1, 99), event(2, 99)])
        self.assertEqual(bot.sent, [(1, "1"), (1, "1")])
        self.assertEqual(len(bot.options[0]["reply_markup"].inline_keyboard[0]), 2)
        fallback = bot.options[1]["reply_markup"].inline_keyboard
        self.assertEqual(len(fallback), 1)
        self.assertEqual(len(fallback[0]), 1)
        self.assertEqual(fallback[0][0].url, "https://t.me/nft/Test-1")
        self.assertEqual(bot.options[0]["link_preview_options"], bot.options[1]["link_preview_options"])
        self.assertEqual(db.finished, {1: "sent", 2: "duplicate_owner"})
        self.assertEqual(db.claim_attempts, 1)

    async def test_valid_owner_button_is_retained(self):
        db, bot = FakeDatabase(), FakeBot()
        await deliver(db, bot, [event(1, 99)])
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(bot.options[0]["reply_markup"].inline_keyboard[0]), 2)

    async def test_unrelated_bad_request_is_not_retried(self):
        db = FakeDatabase()
        bot = FakeBot(errors=[BadRequest("Bad Request: can't parse entities")])
        await deliver(db, bot, [event(1, 99)])
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(db.finished[1], "failed")

    async def test_failed_fallback_does_not_loop_or_release_owner(self):
        db = FakeDatabase()
        bot = FakeBot(errors=[BadRequest("BUTTON_USER_INVALID"), DeliveryError("Connection interrupted")])
        await deliver(db, bot, [event(1, 99), event(2, 99)])
        self.assertEqual(len(bot.sent), 2)
        self.assertEqual(db.finished, {1: "failed", 2: "duplicate_owner"})

    async def test_same_owner_across_categories_receives_one_alert(self):
        db, bot = FakeDatabase(), FakeBot()
        await deliver(db, bot, [event(1, 99), event(2, 99, 200), event(3, 98)])
        self.assertEqual(bot.sent, [(1, "1"), (1, "3")])
        self.assertEqual(db.finished[2], "duplicate_owner")
        self.assertEqual(db.claim_attempts, 2)

    async def test_restart_and_recipient_change_keep_owner_history(self):
        db, bot = FakeDatabase(), FakeBot()
        await deliver(db, bot, [event(1, 99)])
        db.active = 2
        await deliver(db, bot, [event(2, 99, recipient_id=2)])
        self.assertEqual(bot.sent, [(1, "1")])
        self.assertEqual(db.finished[2], "duplicate_owner")

    async def test_skipped_alert_does_not_reserve_owner(self):
        db, bot = FakeDatabase(), FakeBot()
        await deliver(db, bot, [event(1, 99, category_id=300), event(2, 99)])
        self.assertEqual(bot.sent, [(1, "2")])
        self.assertEqual(db.finished[1], "skipped")

    async def test_uncertain_delivery_keeps_reservation(self):
        db, failing = FakeDatabase(), FakeBot(fail=True)
        await deliver(db, failing, [event(1, 99)])
        self.assertEqual(db.finished[1], "failed")
        bot = FakeBot()
        await deliver(db, bot, [event(2, 99)])
        self.assertEqual(bot.sent, [])

    async def test_two_workers_only_one_sends_for_owner(self):
        db, bot = FakeDatabase(), FakeBot()
        await asyncio.gather(deliver(db, bot, [event(1, 99)]),
                             deliver(db, bot, [event(2, 99)]))
        self.assertEqual(len(bot.sent), 1)

    async def test_memory_cache_eviction_does_not_allow_duplicate(self):
        db, bot = FakeDatabase(), FakeBot()
        entries = [event(index + 1, index + 10) for index in range(2001)]
        entries.append(event(3000, 10))
        await deliver(db, bot, entries)
        self.assertEqual(len(bot.sent), 2001)
        self.assertEqual(db.finished[3000], "duplicate_owner")


if __name__ == "__main__":
    unittest.main()
