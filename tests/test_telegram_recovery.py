"""Exercise the real async retry/control flow without Telegram or credentials."""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1] / "telegram_gifts_bot"


class InternalServerError(Exception):
    pass


class FloodWait(Exception):
    def __init__(self, value):
        self.value = value


def load_method(filename, class_name, method_name, namespace):
    path = ROOT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == method_name)
    namespace = {"log": logging.getLogger("test_recovery"),
                 "InternalServerError": InternalServerError, "FloodWait": FloodWait, **namespace}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[method_name]


class TelegramRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def gateway(self, responses):
        clock = [0.0]
        pauses = []

        async def sleep(delay):
            pauses.append(delay)
            clock[0] += delay

        invoke = load_method("services/telegram_client.py", "TelegramGateway", "invoke", {
            "asyncio": SimpleNamespace(sleep=sleep), "monotonic": lambda: clock[0],
        })
        gateway = SimpleNamespace(lock=asyncio.Lock(), next_request=0.0, interval=0.4,
                                  base_interval=0.4, successes=0,
                                  client=SimpleNamespace(invoke=AsyncMock(side_effect=responses)))
        return invoke, gateway, pauses

    async def test_server_error_recovers_with_increasing_delay(self):
        invoke, gateway, pauses = self.gateway([InternalServerError(), InternalServerError(), "ok"])
        self.assertEqual(await invoke(gateway, object()), "ok")
        self.assertEqual(pauses, [5.0, 10.0])
        self.assertEqual(gateway.client.invoke.await_count, 3)

    async def test_persistent_server_error_has_bounded_retries(self):
        invoke, gateway, pauses = self.gateway([InternalServerError()] * 4)
        with self.assertRaises(InternalServerError):
            await invoke(gateway, object())
        self.assertEqual(pauses, [5.0, 10.0, 20.0])
        self.assertEqual(gateway.client.invoke.await_count, 4)
        self.assertGreater(gateway.next_request, 35.0)

    async def test_flood_wait_is_still_respected(self):
        invoke, gateway, pauses = self.gateway([FloodWait(20), "ok"])
        self.assertEqual(await invoke(gateway, object()), "ok")
        self.assertEqual(pauses, [21.0])
        self.assertEqual(gateway.interval, 2.0)

    async def test_other_errors_are_not_retried_as_server_errors(self):
        invoke, gateway, pauses = self.gateway([ValueError("Invalid input")])
        with self.assertRaises(ValueError):
            await invoke(gateway, object())
        self.assertEqual(pauses, [])

    async def test_startup_recovers_after_exhausted_server_retries(self):
        timeouts = []

        async def instant_timeout(coro, timeout):
            coro.close()
            timeouts.append(timeout)
            raise asyncio.TimeoutError

        run = load_method("services/scanner.py", "Scanner", "run", {
            "asyncio": SimpleNamespace(wait_for=instant_timeout, TimeoutError=asyncio.TimeoutError,
                                       CancelledError=asyncio.CancelledError),
        })
        stop = asyncio.Event()

        async def scan_once(emit=True):
            if emit:
                stop.set()

        scanner = SimpleNamespace(stop_event=stop, config=SimpleNamespace(poll_interval=2),
                                  warm=AsyncMock(side_effect=[InternalServerError(), None]),
                                  scan_once=AsyncMock(side_effect=scan_once))
        await run(scanner)
        self.assertEqual(scanner.warm.await_count, 2)
        self.assertEqual(scanner.scan_once.await_count, 2)
        self.assertEqual(timeouts[0], 30)

    async def test_interrupted_baseline_does_not_poison_memory_cache(self):
        cache_globals = {}
        exec((ROOT / "services/cache.py").read_text(encoding="utf-8"), cache_globals)
        scan_once = load_method("services/scanner.py", "Scanner", "scan_once", {
            "raw": SimpleNamespace(functions=SimpleNamespace(payments=SimpleNamespace(
                GetResaleStarGifts=lambda **kwargs: kwargs))),
            "parse_gift": lambda item, users: item,
        })
        gift1, gift2 = SimpleNamespace(id=1), SimpleNamespace(id=2)
        page1 = SimpleNamespace(gifts=[gift1], users=[], next_offset="")
        page2 = SimpleNamespace(gifts=[gift2], users=[], next_offset="")
        db = SimpleNamespace(snapshot=AsyncMock(return_value=(1, 0, None)), claim_many=AsyncMock())
        scanner = SimpleNamespace(config=SimpleNamespace(admin_id=1, max_pages=1, page_size=20),
                                  db=db, stop_event=asyncio.Event(), categories=[100, 200],
                                  gateway=SimpleNamespace(invoke=AsyncMock(side_effect=[
                                      page1, InternalServerError(), page1, page2])),
                                  catalog=SimpleNamespace(learn=lambda gift: None),
                                  recent=cache_globals["RecentIDs"]())
        with self.assertRaises(InternalServerError):
            await scan_once(scanner, emit=False)
        self.assertNotIn(1, scanner.recent)
        db.claim_many.assert_not_awaited()
        await scan_once(scanner, emit=False)
        db.claim_many.assert_awaited_once_with([gift1, gift2], 1)
        self.assertIn(1, scanner.recent)
        self.assertIn(2, scanner.recent)


if __name__ == "__main__":
    unittest.main()
