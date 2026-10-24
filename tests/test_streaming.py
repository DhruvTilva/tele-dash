"""Parallel chunk streaming returns exactly the requested bytes, in order, and reuses its cache."""
import asyncio
import os
import random
from types import SimpleNamespace

import pytest

from app import sources
from app.sources import CHUNK, TelegramSource

SIZE = 7 * CHUNK + 12345
DATA = os.urandom(SIZE)


class FakeIter:
    """Mimics Telethon's iter_download(offset, stride): chunk after chunk, each `stride` bytes apart."""
    def __init__(self, client, offset, stride, file_size):
        self.client, self.offset, self.stride, self.size = client, offset, stride, file_size

    async def __aenter__(self): return self
    async def __aexit__(self, *a): self.client.open_iters -= 1
    def __aiter__(self): return self

    async def __anext__(self):
        if self.offset >= self.size:
            raise StopAsyncIteration
        await asyncio.sleep(random.random() * 0.01)  # chunks finish out of order across lanes
        self.client.requested.append(self.offset // CHUNK)
        chunk = DATA[self.offset:self.offset + CHUNK]
        self.offset += self.stride
        return chunk


class FakeClient:
    def __init__(self): self.open_iters, self.requested = 0, []

    def iter_download(self, doc, offset, stride, request_size, chunk_size, file_size):
        self.open_iters += 1
        return FakeIter(self, offset, stride, file_size)


@pytest.fixture
def src(tmp_path):
    s = TelegramSource(FakeClient(), tmp_path)
    s._msgs[(1, 1)] = (1e18, SimpleNamespace(document=object(), file=SimpleNamespace(size=SIZE)))
    return s


async def read(src, start, end):
    return b"".join([c async for c in src.stream(1, 1, start, end)])


@pytest.mark.parametrize("start,end", [(0, SIZE - 1), (5, 100), (CHUNK - 3, CHUNK + 3), (3 * CHUNK + 7, SIZE - 1),
                                       (SIZE - 10, SIZE - 1), (0, 0), (2 * CHUNK, 4 * CHUNK - 1)])
def test_ranges_are_exact(src, start, end):
    assert asyncio.run(read(src, start, end)) == DATA[start:end + 1]


def test_second_read_comes_from_cache(src):
    asyncio.run(read(src, 0, 3 * CHUNK))
    fetched = len(src.client.requested)
    assert asyncio.run(read(src, 100, 2 * CHUNK)) == DATA[100:2 * CHUNK + 1]
    assert len(src.client.requested) == fetched


def test_nothing_fetched_beyond_the_range_and_lanes_are_closed(src, monkeypatch):
    monkeypatch.setattr(sources, "LANES", 4)
    asyncio.run(read(src, 0, 2 * CHUNK - 1))
    assert sorted(src.client.requested) == [0, 1]
    assert src.client.open_iters == 0


def test_abandoned_stream_cleans_up(src):
    async def go():
        gen = src.stream(1, 1, 0, SIZE - 1)
        await gen.__anext__()
        await gen.aclose()
    asyncio.run(go())
    assert src.client.open_iters == 0


def test_cache_is_bounded(src, monkeypatch):
    monkeypatch.setattr(sources, "CACHE_BYTES", 2 * CHUNK)
    asyncio.run(read(src, 0, SIZE - 1))
    assert src._cache_size <= 2 * CHUNK


def test_real_telethon_downloader_uses_one_1mb_request_per_chunk():
    """Telethon's own iter_download (with a fake network underneath) honours offset/stride at 1 MB."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl import types

    requests = []

    async def fake_call(sender, req):
        requests.append((req.offset // CHUNK, req.limit))
        return SimpleNamespace(bytes=DATA[req.offset:req.offset + req.limit])

    async def go():
        client = TelegramClient(StringSession(), 1, "x")
        client._call = fake_call
        loc = types.InputDocumentFileLocation(id=1, access_hash=1, file_reference=b"", thumb_size="")
        got, idx = [], 2
        async with client.iter_download(loc, offset=idx * CHUNK, stride=3 * CHUNK, request_size=CHUNK,
                                        chunk_size=CHUNK, file_size=SIZE) as it:
            async for chunk in it:
                got.append((idx, bytes(chunk)))
                idx += 3
                if idx > 7:  # last chunk of the file, like the streamer's lanes
                    break
        return got

    got = asyncio.run(go())
    assert [i for i, _ in got] == [2, 5]
    assert all(b == DATA[i * CHUNK:(i + 1) * CHUNK] for i, b in got)
    assert requests == [(2, CHUNK), (5, CHUNK)]
