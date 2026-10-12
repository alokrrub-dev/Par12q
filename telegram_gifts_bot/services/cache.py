from collections import OrderedDict, deque
from time import monotonic


class RecentIDs:
    def __init__(self, capacity: int = 2000):
        self.capacity = capacity
        self.order = deque()
        self.ids: set[int] = set()

    def __contains__(self, key: int) -> bool:
        return key in self.ids

    def add(self, key: int):
        if key in self.ids:
            return
        if len(self.order) >= self.capacity:
            self.ids.remove(self.order.popleft())
        self.order.append(key)
        self.ids.add(key)


class TTLCache:
    def __init__(self, ttl: float = 900, capacity: int = 10000):
        self.ttl = ttl
        self.capacity = capacity
        self.entries = OrderedDict()

    def get(self, key):
        item = self.entries.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= monotonic():
            del self.entries[key]
            return None
        self.entries.move_to_end(key)
        return value

    def put(self, key, value):
        self.entries[key] = (monotonic() + self.ttl, value)
        self.entries.move_to_end(key)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
