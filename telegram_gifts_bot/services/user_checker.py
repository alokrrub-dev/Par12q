from dataclasses import dataclass

from pyrogram import raw
from pyrogram.errors import RPCError

from services.cache import TTLCache


@dataclass(frozen=True, slots=True)
class Seller:
    user_id: int
    username: str | None
    name: str
    level: int | None
    eligible: bool


class UserChecker:
    def __init__(self, gateway):
        self.gateway = gateway
        self.cache = TTLCache(ttl=900, capacity=10000)

    async def check(self, user_id: int, user=None) -> Seller:
        cached = self.cache.get(user_id)
        if cached is not None:
            return cached
        denied = Seller(user_id, None, str(user_id), None, False)
        try:
            if user is not None and not getattr(user, "min", False):
                access_hash = getattr(user, "access_hash", None)
                if access_hash is None:
                    return denied
                peer = raw.types.InputUser(user_id=user_id, access_hash=access_hash)
            else:
                resolved = await self.gateway.client.resolve_peer(user_id)
                if not isinstance(resolved, raw.types.InputPeerUser):
                    return denied
                peer = raw.types.InputUser(user_id=resolved.user_id, access_hash=resolved.access_hash)
            result = await self.gateway.invoke(raw.functions.users.GetFullUser(id=peer))
        except RPCError:
            # A temporary failure isn't proof of ineligibility: retry on a later poll.
            return denied
        full = result.full_user
        actual = next((entry for entry in result.users if entry.id == user_id), user)
        if actual is None:
            return denied
        rating = getattr(full, "stars_rating", None)
        # Telegram omits stars_rating for accounts without a rating; that is
        # the initial level 0, not an invalid seller.
        level = rating.level if rating is not None else 0
        public_charge = getattr(actual, "send_paid_messages_stars", None) or 0
        session_charge = getattr(full, "send_paid_messages_stars", None) or 0
        # Reject public paid messaging even if SESSION_STRING has a personal exemption.
        eligible = (level in (0, 1) and public_charge == 0 and session_charge == 0
                    and not getattr(actual, "deleted", False)
                    and not getattr(actual, "bot", False)
                    and not getattr(full, "blocked", False)
                    and not getattr(full, "contact_require_premium", False))
        username = getattr(actual, "username", None)
        if not username:
            username = next((entry.username for entry in getattr(actual, "usernames", None) or []
                             if entry.active), None)
        name = " ".join(filter(None, (getattr(actual, "first_name", None),
                                       getattr(actual, "last_name", None)))) or str(user_id)
        seller = Seller(user_id, username, name, level, eligible)
        self.cache.put(user_id, seller)
        return seller
