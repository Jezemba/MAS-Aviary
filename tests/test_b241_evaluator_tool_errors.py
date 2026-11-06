"""B241 ladder (2026-11-06): an MCP tool error comes back as plain text; the evaluator must count it as a failure."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "optimizer_baselines"))

import evaluator  # noqa: E402


def test_plain_text_reply_is_an_error():
    out = evaluator._json("Error executing tool generate_volume_mesh: Mesh deformation failed: too far")
    assert "Mesh deformation failed" in out["error"]


def test_json_replies_pass_through():
    assert evaluator._json('{"success": true, "n": 2}') == {"success": True, "n": 2}
    assert evaluator._json({"a": 1}) == {"a": 1}
