import json

from benchmark.wiki.src.vikingbot_runner import _loads_vikingbot_json


def test_loads_vikingbot_json_preserves_valid_latex_backslashes():
    payload = {"text": r"state posterior $\\pi _{jm}$", "trace": []}

    parsed = _loads_vikingbot_json(json.dumps(payload, ensure_ascii=False))

    assert parsed["text"] == payload["text"]


def test_loads_vikingbot_json_repairs_invalid_answer_backslashes():
    raw = r'{"text":"bad latex \q stays readable","trace":[]}'

    parsed = _loads_vikingbot_json(raw)

    assert parsed["text"] == r"bad latex \q stays readable"
