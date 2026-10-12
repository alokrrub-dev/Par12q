import asyncio
import logging
from time import monotonic

from pyrogram import Client, raw
from pyrogram.errors import FloodWait, InternalServerError

log = logging.getLogger(__name__)


class TelegramGateway:
    def __init__(self, config):
        if not hasattr(raw.functions.payments, "GetResaleStarGifts"):
            raise RuntimeError("Установите Kurigram из requirements.txt вместо старого Pyrogram")
        self.client = Client(
            "gifts_tracker", api_id=config.api_id, api_hash=config.api_hash,
            session_string=config.session_string, in_memory=True,
            no_updates=True, sleep_threshold=0,
        )
        self.interval = config.request_interval
        self.base_interval = config.request_interval
        self.successes = 0
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.connected = False
        self.user_id: int | None = None

    async def start(self):
        # No start()/authorize(): an invalid session must never prompt for an SMS.
        authorized = await self.client.connect()
        self.connected = True
        if not authorized:
            raise RuntimeError("SESSION_STRING не содержит готовую авторизацию")
        me = await self.client.get_me()
        if me.is_bot:
            raise RuntimeError("Нужна String Session пользовательского аккаунта")
        self.user_id = me.id

    async def invoke(self, query):
        method_name = type(query).__name__
        server_retries = 0
        async with self.lock:
            while True:
                delay = self.next_request - monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self.next_request = monotonic() + self.interval
                try:
                    result = await self.client.invoke(query, sleep_threshold=0)
                    self.successes += 1
                    if self.successes >= 100:
                        self.interval = max(self.base_interval, self.interval * 0.85)
                        self.successes = 0
                    return result
                except FloodWait as error:
                    seconds = max(1, error.value)
                    # Do not immediately return to the rate that caused FloodWait.
                    # A 35-second wait is a signal to slow down all subsequent calls.
                    self.interval = min(2.0, max(self.interval * 2, seconds / 10))
                    self.successes = 0
                    self.next_request = monotonic() + seconds + 1
                    log.warning("MTProto FloodWait: %s seconds for %s; adaptive interval now %.2fs",
                                seconds, method_name, self.interval)
                except InternalServerError as error:
                    # Pyrogram already retries 500 errors internally. If those
                    # retries fail, wait longer before trying the same read again.
                    # This gateway is used exclusively for read-only queries.
                    delay = min(30.0, 5.0 * 2**server_retries)
                    self.next_request = monotonic() + delay
                    self.successes = 0
                    if server_retries >= 3:
                        raise
                    server_retries += 1
                    log.warning("MTProto server error %s for %s; retry %d/3 in %.0fs",
                                type(error).__name__, method_name, server_retries, delay)

    async def close(self):
        if self.connected:
            await self.client.disconnect()
            self.connected = False
