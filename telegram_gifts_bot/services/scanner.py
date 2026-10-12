import asyncio
import logging
from dataclasses import dataclass

from pyrogram import raw
from pyrogram.errors import InternalServerError

from services.cache import RecentIDs
from services.market import Catalog, Gift, matches, parse_gift

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GiftEvent:
    gift: Gift
    seller: object
    recipient_id: int


class Scanner:
    """Polls Telegram's resale feed. Telegram does not expose a push update for new listings."""

    def __init__(self, config, db, gateway, checker, out_queue: asyncio.Queue[GiftEvent]):
        self.config, self.db, self.gateway = config, db, gateway
        self.checker, self.out_queue = checker, out_queue
        self.catalog = Catalog(gateway)
        self.recent = RecentIDs(2000)
        self.stop_event = asyncio.Event()
        self.categories: list[int] = []

    async def warm(self):
        for gift_id in await self.db.recent_ids():
            self.recent.add(gift_id)
        await self.catalog.refresh(force=True)
        self.categories = list(self.catalog.items)
        if not self.categories:
            log.warning("Telegram returned no resale-capable gift categories")

    async def stop(self):
        self.stop_event.set()

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await self.warm()
                # Do not send the inventory that already existed at startup.
                await self.scan_once(emit=False)
                break
            except InternalServerError:
                log.warning("Telegram is unavailable during scanner initialization; retry in 30s")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), 30)
                except asyncio.TimeoutError:
                    pass
        while not self.stop_event.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scanner iteration failed")
            try:
                await asyncio.wait_for(self.stop_event.wait(), self.config.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def scan_once(self, emit: bool = True):
        recipient_id, _, filters = await self.db.snapshot(self.config.admin_id)
        if emit and filters is not None and not filters.parsing_enabled:
            return
        if emit and (filters is None or not filters.selected_categories):
            return
        baseline_gifts = {}
        stats = {"pages": 0, "items": 0, "parsed": 0, "eligible": 0, "queued": 0}
        for category_id in self.categories:
            if self.stop_event.is_set():
                return
            if emit and (filters is None or str(category_id) not in filters.selected_categories):
                continue
            offset = ""
            for _page in range(self.config.max_pages):
                response = await self.gateway.invoke(raw.functions.payments.GetResaleStarGifts(
                    gift_id=category_id, offset=offset, limit=self.config.page_size,
                ))
                stats["pages"] += 1
                users = {entry.id: entry for entry in getattr(response, "users", [])}
                page_items = getattr(response, "gifts", [])
                stats["items"] += len(page_items)
                for item in page_items:
                    gift = parse_gift(item, users)
                    if gift is None:
                        continue
                    stats["parsed"] += 1
                    self.catalog.learn(gift)
                    if not emit:
                        if gift.id not in self.recent:
                            baseline_gifts[gift.id] = gift
                        continue
                    if gift.id in self.recent or not matches(gift, filters):
                        continue
                    seller = await self.checker.check(gift.owner_id, gift.owner)
                    if not seller.eligible:
                        continue
                    stats["eligible"] += 1
                    # Fast cache is filled only after seller and filter validation, avoiding cache poisoning.
                    if gift.id in self.recent or not await self.db.claim(gift, recipient_id):
                        self.recent.add(gift.id)
                        continue
                    self.recent.add(gift.id)
                    await self.out_queue.put(GiftEvent(gift, seller, recipient_id))
                    stats["queued"] += 1
                next_offset = getattr(response, "next_offset", None) or ""
                if not page_items or not next_offset or next_offset == offset:
                    break
                offset = next_offset
        if not emit:
            # Cache only after the baseline is committed; a failed page request
            # must not make an initialization retry lose uncommitted gift IDs.
            await self.db.claim_many(list(baseline_gifts.values()), recipient_id)
            for gift_id in baseline_gifts:
                self.recent.add(gift_id)
            log.info("baseline inventory: pages=%d items=%d parsed=%d saved=%d",
                     stats["pages"], stats["items"], stats["parsed"], len(baseline_gifts))
        else:
            log.info("scan: pages=%d items=%d parsed=%d eligible=%d queued=%d",
                     stats["pages"], stats["items"], stats["parsed"],
                     stats["eligible"], stats["queued"])
