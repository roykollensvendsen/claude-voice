"""The fake refuses what the real gizmo refuses, so a test cannot pass that the device would fail."""

import asyncio

import pytest

from gizmo_mcp.device import NotSetUpError, SlowDownError, Unconnected, UnreachableError
from gizmo_mcp.fake import READS_PER_MINUTE, WINDOW, FakeGizmo


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_fake_refuses_a_read_sooner_than_the_device_allows():
    clock = _Clock()
    gizmo = FakeGizmo(clock=clock)
    for _ in range(READS_PER_MINUTE):
        asyncio.run(gizmo.read())
    with pytest.raises(SlowDownError) as slow:
        asyncio.run(gizmo.read())
    assert slow.value.seconds == WINDOW


def test_the_fake_answers_again_once_the_minute_has_passed():
    clock = _Clock()
    gizmo = FakeGizmo(clock=clock)
    for _ in range(READS_PER_MINUTE):
        asyncio.run(gizmo.read())
    clock.now = WINDOW
    assert asyncio.run(gizmo.read()).level == 40


def test_a_fake_off_the_network_answers_nothing():
    gizmo = FakeGizmo()
    gizmo.unreachable = True
    with pytest.raises(UnreachableError):
        asyncio.run(gizmo.read())


def test_an_unconnected_gizmo_says_what_to_do():
    with pytest.raises(NotSetUpError, match="--simulate"):
        asyncio.run(Unconnected().read())
