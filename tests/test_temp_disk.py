"""The shared temporary disk is watched, so it never fills up unseen (#434).

On 21.11 it filled, and every session on the machine stopped. The bridge now measures it every
ten minutes, read-only. Past 80 % it names the largest folders; past 90 % it says so to the voice.
"""

import os

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import temp_disk
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def folder_of(tmp_path, name, files):
    folder = tmp_path / name
    folder.mkdir(parents=True)
    for i, size in enumerate(files):
        (folder / f"f{i}").write_bytes(b"x" * size)
    return folder


def test_a_folder_is_sized_with_everything_under_it(tmp_path):
    folder_of(tmp_path, "a/deep/er", [4096, 8192])
    assert temp_disk.size_of(tmp_path / "a") >= 12288


def test_a_link_out_of_a_folder_is_not_followed(tmp_path):
    big = folder_of(tmp_path, "elsewhere", [1 << 20])
    small = folder_of(tmp_path, "mine", [10])
    os.symlink(big, small / "link")
    assert temp_disk.size_of(small) < 1 << 20


def test_a_folder_that_cannot_be_read_counts_nothing(tmp_path):
    assert temp_disk.size_of(tmp_path / "gone") == 0


def test_the_largest_folders_come_first_and_at_most_five(tmp_path):
    for i in range(7):
        folder_of(tmp_path, f"s{i}", [(i + 1) * 100_000])
    (tmp_path / "loose-file").write_bytes(b"x" * 10_000_000)  # only folders are named
    largest = temp_disk.largest(tmp_path)
    assert [f["folder"] for f in largest] == ["s6", "s5", "s4", "s3", "s2"]


def test_no_root_yet_has_no_largest_folders(tmp_path):
    assert temp_disk.largest(tmp_path / "none") == []


def reading(percent):
    return {"percent": percent, "largest": [{"folder": "s1", "mb": 2100}, {"folder": "s2", "mb": 900}]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watch(levels):
    clock, seen = Clock(), iter(levels)
    return temp_disk.Watch(lambda: reading(next(seen)), clock=clock), clock


def test_the_temporary_disk_is_reported_once_each_time_it_passes_a_level():
    w, clock = watch([50, 82, 85, 91, 93])
    said = []
    for _ in range(5):
        said.append(w.look())
        clock.now += temp_disk.EVERY
    assert said[0] is None and said[2] is None and said[4] is None
    assert "82 %" in said[1] and "s1 (2.1 GB)" in said[1] and "s2 (900 MB)" in said[1]
    assert "91 %" in said[3] and "sessions stop" in said[3]


def test_a_temporary_disk_that_falls_back_is_reported_again_when_it_passes_a_level_next():
    w, clock = watch([85, 60, 84])
    said = []
    for _ in range(3):
        said.append(w.look())
        clock.now += temp_disk.EVERY
    assert said[0] and said[1] is None and said[2] and "84 %" in said[2]


def test_the_temporary_disk_is_measured_at_most_every_ten_minutes():
    measured = []
    clock = Clock()
    w = temp_disk.Watch(lambda: measured.append(1) or reading(10), clock=clock)
    w.look()
    clock.now += temp_disk.EVERY - 1
    w.look()
    assert len(measured) == 1
    clock.now += 1
    w.look()
    assert len(measured) == 2 and w.last["percent"] == 10


def test_a_measure_that_fails_is_tried_again_next_time():
    clock, calls = Clock(), []

    def measure():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("gone")
        return reading(85)

    w = temp_disk.Watch(measure, clock=clock)
    assert w.look() is None and w.last is None
    clock.now += temp_disk.EVERY
    assert "85 %" in w.look()


def test_a_reading_has_the_share_used_and_the_largest_folders(tmp_path):
    folder_of(tmp_path, "s1", [50_000])
    out = temp_disk.reading(tmp_path, tmp_path)
    assert 0 <= out["percent"] <= 100 and out["largest"][0]["folder"] == "s1"
    assert out["total_gb"] > 0 and "at" in out


def bridge(tmp_path, levels):
    clock = Clock()
    seen = iter(levels)
    watch = temp_disk.Watch(lambda: reading(next(seen)), clock=clock)
    (tmp_path / "live").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude())
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        read_transcript=lambda s, d: [],
        org_folders=[],
        temp_watch=watch,
        proof=True,  # starts no plugins, whatever settings it finds
    )
    return server, clock


async def call(server, name, **args):
    async with Client(server) as c:
        return (await c.call_tool(name, args)).structured_content


async def test_a_temporary_disk_past_a_level_is_news_for_the_voice(tmp_path):
    server, clock = bridge(tmp_path, [70, 92])
    cursor = (await call(server, "whats_new"))["cursor"]
    server.temp_disk_check()
    clock.now += temp_disk.EVERY
    server.temp_disk_check()
    news = (await call(server, "whats_new", cursor=cursor))["events"]
    said = [e["text"] for e in news if e["kind"] == "temp_disk"]
    assert len(said) == 1 and "92 %" in said[0] and "s1 (2.1 GB)" in said[0]


async def test_the_health_page_shows_the_latest_reading_of_the_temporary_disk(tmp_path):
    server, _ = bridge(tmp_path, [42])
    assert (await call(server, "health"))["temp_disk"] is None
    server.temp_disk_check()
    assert (await call(server, "health"))["temp_disk"]["percent"] == 42
