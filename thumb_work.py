"""Supersede waiting thumbnail work per browser tab, without changing access rights."""
import re
import threading
import time
from contextlib import contextmanager


class Superseded(Exception):
    pass


class Views:
    def __init__(self, limit=4096, ttl=3600):
        self.lock = threading.Lock()
        self.latest = {}
        self.limit, self.ttl = limit, ttl

    def register(self, identity, tab, epoch):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', tab or ''):
            return None
        try:
            epoch = int(epoch)
        except (ValueError, TypeError):
            return None
        if not 0 <= epoch <= 9007199254740991:
            return None
        key = (identity, tab)
        now = time.monotonic()
        with self.lock:
            for old, (_, at) in list(self.latest.items()):
                if now - at > self.ttl:
                    del self.latest[old]
            previous = self.latest.get(key, (-1, now))[0]
            self.latest[key] = (max(previous, epoch), now)
            while len(self.latest) > self.limit:
                del self.latest[min(self.latest, key=lambda k: self.latest[k][1])]
        def obsolete():
            with self.lock:
                current = self.latest.get(key)
                return current is None or current[0] != epoch
        return obsolete


views = Views()
local = threading.local()


@contextmanager
def demand(cancelled):
    previous = getattr(local, 'cancelled', None)
    local.cancelled = cancelled
    try:
        yield
    finally:
        local.cancelled = previous


def check():
    cancelled = getattr(local, 'cancelled', None)
    if cancelled and cancelled():
        raise Superseded()
