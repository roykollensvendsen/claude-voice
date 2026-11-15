"""The layers: the core, then the server and the command line.

The core asks the gizmo and words the answers; it knows no MCP. Only the server and
the command line know MCP and put the core together. Every import,
including those made only for type checking or inside a function, is counted.

The map of the layers is in `scripts/ratchet.py`, which also counts the breaches
for the code-health page; here any breach at all is refused.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
PACKAGE = ROOT / "src" / "gizmo_mcp"


def _ratchet():
    spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts/ratchet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ratchet = _ratchet()


def test_every_module_has_a_layer_and_imports_only_what_its_layer_may():
    assert ratchet.layer_breaches(PACKAGE) == []


def test_the_core_never_imports_mcp_or_the_top():
    assert "mcp" in ratchet.FORBIDDEN["core"]
    assert "top" not in ratchet.ALLOWED["core"]
