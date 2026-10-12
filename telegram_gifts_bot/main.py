import asyncio
import logging
import os
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import Config
from database.connection import Database
from handlers.router2 import build_router
from services.notifier import notifier_loop
from services.scanner import Scanner
from services.telegram_client import TelegramGateway
from services.user_checker import UserChecker


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def create_http_server(port: int):
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/ready", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner


async def run(config: Config):
    db = Database(config.database_url)
    gateway = TelegramGateway(config)
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    queue = asyncio.Queue(maxsize=config.queue_size)
    stop = asyncio.Event()
    scanner = Scanner(config, db, gateway, UserChecker(gateway), queue)
    dispatcher.include_router(build_router(config, db, scanner))
    tasks: list[asyncio.Task] = []
    runner = None
    try:
        await db.initialize()
        await gateway.start()
        runner = await create_http_server(config.port)
        tasks = [asyncio.create_task(scanner.run(), name="gift-scanner"),
                 asyncio.create_task(notifier_loop(config, db, bot, queue, stop), name="gift-notifier")]
        logging.info("Telegram Gifts tracker is running on port %s", config.port)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        stop.set()
        await scanner.stop()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if runner is not None:
            await runner.cleanup()
        with suppress(Exception):
            await gateway.close()
        with suppress(Exception):
            await bot.session.close()
        await db.close()


def main():
    config = Config.load()
    logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
