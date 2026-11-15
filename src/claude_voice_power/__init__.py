"""claude-voice-power: shuts a machine down or restarts it, only on a yes the hub signed.

This is a package of its own. It is built as a wheel on a release tag and installed by a root
step, outside every home folder, so nothing a Claude session can edit is ever run by it. It
imports nothing from claude_voice.
"""
