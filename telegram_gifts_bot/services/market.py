from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from urllib.parse import quote

from pyrogram import raw


@dataclass(frozen=True, slots=True)
class Gift:
    id: int
    category_id: int
    slug: str
    title: str
    number: int
    owner_id: int
    owner: object | None
    prices: dict[str, Decimal]
    attributes: tuple[str, ...]

    @property
    def url(self) -> str:
        return "https://t.me/nft/" + quote(self.slug, safe="-_")


def parse_gift(item, users: dict[int, object]) -> Gift | None:
    # Kurigram can expose the constructor from a newer generated schema class;
    # use its stable TL fields instead of relying only on isinstance().
    if not all(hasattr(item, field) for field in ("id", "gift_id", "slug", "owner_id")):
        return None
    owner = getattr(item, "owner_id", None)
    if not hasattr(owner, "user_id"):
        return None
    if not item.slug or getattr(item, "burned", False):
        return None
    prices: dict[str, Decimal] = {}
    amounts = getattr(item, "resell_amount", None)
    if isinstance(amounts, int):
        prices["XTR"] = Decimal(amounts)
    else:
        for amount in amounts or []:
            kind = type(amount).__name__
            if kind == "StarsAmount":
                prices["XTR"] = Decimal(amount.amount) + Decimal(amount.nanos) / 10**9
            elif kind == "StarsTonAmount":
                prices["TON"] = Decimal(amount.amount) / 10**9
    if getattr(item, "resale_ton_only", False):
        prices.pop("XTR", None)
    prices = {currency: value for currency, value in prices.items() if value > 0}
    if not prices:
        return None
    labels = {"StarGiftAttributeModel": "Модель", "StarGiftAttributePattern": "Символ",
              "StarGiftAttributeBackdrop": "Фон"}
    attributes = []
    for attribute in getattr(item, "attributes", []) or []:
        label = labels.get(type(attribute).__name__)
        if label:
            rarity = getattr(attribute, "rarity_permille", None)
            suffix = f" ({Decimal(rarity) / 10}%)" if rarity is not None else ""
            attributes.append(f"{label}: {attribute.name}{suffix}")
    return Gift(item.id, item.gift_id, item.slug, item.title, item.num,
                owner.user_id, users.get(owner.user_id), prices, tuple(attributes))


def matches(gift: Gift, filters) -> bool:
    if filters is None:
        return True
    if not filters.parsing_enabled:
        return False
    if not filters.selected_categories:
        return False
    if str(gift.category_id) not in filters.selected_categories:
        return False
    currency = filters.price_currency
    if currency == "ANY":
        return filters.max_price is None or any(
            price <= filters.max_price for price in gift.prices.values())
    price = gift.prices.get(currency)
    return price is not None and (filters.max_price is None or price <= filters.max_price)


class Catalog:
    def __init__(self, gateway):
        self.gateway = gateway
        self.items: dict[int, str] = {}
        self.hash = 0
        self.refreshed = 0.0

    async def refresh(self, force: bool = False):
        if self.items and not force and monotonic() - self.refreshed < 900:
            return
        result = await self.gateway.invoke(raw.functions.payments.GetStarGifts(hash=self.hash))
        if hasattr(result, "gifts"):
            items = {}
            for gift in result.gifts:
                if not (getattr(gift, "availability_resale", 0)
                        or getattr(gift, "upgrade_stars", None) is not None):
                    continue
                items[gift.id] = self._label(gift)
            self.items = items
            self.hash = result.hash
        self.refreshed = monotonic()

    def learn(self, gift: Gift):
        # Keep a useful base-gift label after resale items are parsed.
        if gift.category_id not in self.items or self.items[gift.category_id].startswith("Подарок "):
            self.items[gift.category_id] = gift.title or f"Подарок {gift.category_id}"

    @staticmethod
    def _label(gift) -> str:
        title = getattr(gift, "title", None)
        if title:
            return str(title)
        emoji = None
        sticker = getattr(gift, "sticker", None)
        for attribute in getattr(sticker, "attributes", []) or []:
            emoji = getattr(attribute, "alt", None) or getattr(attribute, "emoji", None)
            if emoji:
                break
        if not emoji:
            file_name = getattr(sticker, "file_name", None)
            if file_name:
                emoji = str(file_name).rsplit("/", 1)[-1].rsplit(".", 1)[0]
        stars = getattr(gift, "stars", None)
        if emoji and stars:
            return f"{emoji} ({stars}⭐)"
        if emoji:
            return str(emoji)
        if stars:
            return f"🎁 ({stars}⭐)"
        return f"Подарок {gift.id}"
