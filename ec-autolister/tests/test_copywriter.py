from types import SimpleNamespace

import pytest

from autolister.content.copywriter import Copywriter, build_prompt
from autolister.models import SourceProduct

SRC = SourceProduct(sku="A", name="ダンク", brand="Nike", model_no="DD1391-100", cost_jpy=1)


class _Stream:
    def __init__(self, msg, captured, kwargs):
        self.msg = msg
        captured.update(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def _client(msg, captured):
    stream = lambda **kw: _Stream(msg, captured, kw)  # noqa: E731
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_parses_structured_output(settings, listing_copy):
    msg = SimpleNamespace(stop_reason="end_turn", stop_details=None,
                          content=[SimpleNamespace(type="text", text=listing_copy.model_dump_json())])
    captured = {}
    out = Copywriter(settings, _client(msg, captured)).write(SRC, None)
    assert out == listing_copy
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["fallbacks"] == "default"


def test_refusal_raises(settings):
    msg = SimpleNamespace(stop_reason="refusal", stop_details={"category": "x"}, content=[])
    with pytest.raises(RuntimeError, match="拒否"):
        Copywriter(settings, _client(msg, {})).write(SRC, None)


def test_prompt_contains_only_given_info():
    p = build_prompt(SRC, None)
    assert "DD1391-100" in p and "カラー" not in p
