"""The tracker: where pushed work stands, read from a work tracker behind one narrow interface.

`base` holds the interface and the three states. `github` is the first adapter, and
reads only. `status` turns items into the lines `claude-voice tracker status --check`
prints, each one through the privacy gate. docs/tracker.md describes the command.
"""
