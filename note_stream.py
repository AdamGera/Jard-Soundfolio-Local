"""Latest displayable draft of an active local writing job. SQLite owns completed versions."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar("note_preview", default=None)


class Channel:
    def __init__(self):
        self.draft = None
        self.changed = asyncio.Event()

    def update(self, draft):
        self.draft = draft
        previous, self.changed = self.changed, asyncio.Event()
        previous.set()


def patch(**fields):
    channel = _current.get()
    if channel is not None and channel.draft is not None:
        channel.update({**channel.draft, **fields})


class Previews:
    def __init__(self):
        self.channels = {}

    @contextmanager
    def watch(self, key, language):
        # A job and all of its viewers share one snapshot. Slow readers skip superseded tokens.
        identity = key, language
        if identity not in self.channels:
            self.channels[identity] = Channel(), 0
        channel, users = self.channels[identity]
        self.channels[identity] = channel, users + 1
        try:
            yield channel
        finally:
            users = self.channels[identity][1] - 1
            if users:
                self.channels[identity] = channel, users
            else:
                del self.channels[identity]

    @contextmanager
    def track(self, key, language):
        with self.watch(key, language) as channel:
            token = _current.set(channel)
            channel.update({"language": language})
            try:
                yield
            finally:
                _current.reset(token)
                channel.update(None)
