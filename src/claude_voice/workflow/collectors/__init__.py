"""The one list of collectors. A new source is its own module here and one entry in COLLECTORS."""

from __future__ import annotations

from claude_voice.workflow.core.protocol import Collector

COLLECTORS: list[Collector] = []
