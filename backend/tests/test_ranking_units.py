"""Unit tests for ranking signals, provider plumbing and small API helpers."""

import pytest

from app.agent.tools import openai_tools, paginate, tool_definitions
from app.core.config import get_settings
from app.core.ratelimit import RateLimiter
from app.llm.models import LLMUnavailable, resolve_model
from app.llm.nvidia import ToolCall, _ThinkSplitter, extract_json
from app.retrieval.hybrid import adjusted_score
from app.retrieval.rerank import sigmoid
from app.retrieval.web import WebSearchError, fetch_page


def _score(**kw):
    base = {
        "age_days": 0.0,
        "recency_bias": "off",
        "boost": 0,
        "feedback_up": 0,
        "feedback_down": 0,
    }
    return adjusted_score(1.0, **{**base, **kw})


def test_neutral_signals_leave_score_unchanged():
    assert _score() == pytest.approx(1.0)
    assert _score(age_days=3000) == pytest.approx(1.0)  # recency off


def test_recency_bias_prefers_fresh_documents_by_strength():
    for bias in ("low", "medium", "high"):
        assert _score(recency_bias=bias, age_days=10) > _score(recency_bias=bias, age_days=400)
    stale = {b: _score(recency_bias=b, age_days=365) for b in ("low", "medium", "high")}
    assert stale["low"] > stale["medium"] > stale["high"] > 0.5


def test_boost_and_feedback_are_bounded():
    assert _score(boost=3) == pytest.approx(1.45) and _score(boost=-3) == pytest.approx(0.55)
    assert _score(boost=99) == _score(boost=3)
    assert 1.0 < _score(feedback_up=2) < _score(feedback_up=50) <= 1.25
    assert 0.75 <= _score(feedback_down=50) < _score(feedback_down=2) < 1.0
    assert _score(feedback_up=3, feedback_down=3) == pytest.approx(1.0)


def test_sigmoid_handles_missing_scores():
    assert sigmoid(float("-inf")) == 0.0 and sigmoid(0) == 0.5 and sigmoid(1000) == 1.0


def test_think_splitter_routes_tagged_reasoning_across_chunks():
    splitter = _ThinkSplitter()
    out = []
    for piece in ("<thi", "nk>plan it</th", "ink>Answer ", "[1]."):
        out += splitter.feed(piece)
    out += splitter.flush()
    reasoning = "".join(t for k, t in out if k == "reasoning")
    text = "".join(t for k, t in out if k == "text")
    assert reasoning == "plan it" and text == "Answer [1]."


def test_extract_json_and_tool_arguments():
    assert extract_json('Sure:\n```json\n{"title": "Deploy rules"}\n```') == {
        "title": "Deploy rules"
    }
    with pytest.raises(ValueError):
        extract_json("no json here")
    assert ToolCall("c1", "search_knowledge", '{"queries": ["x"]}').parsed() == {"queries": ["x"]}
    assert ToolCall("c1", "list_collections", "").parsed() == {}
    with pytest.raises(ValueError):
        ToolCall("c1", "search_knowledge", '{"queries": [').parsed()


def test_resolve_model_falls_back_to_the_configured_provider(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", "")
    monkeypatch.setattr(s, "nvidia_api_key", "")
    with pytest.raises(LLMUnavailable):
        resolve_model("claude-opus-5-5")
    monkeypatch.setattr(s, "nvidia_api_key", "nv-test")
    assert resolve_model("claude-opus-5-5") == ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b")
    assert resolve_model("minimaxai/minimax-m3.1-flash")[1] == "minimaxai/minimax-m3.1-flash"
    assert resolve_model(None)[0] == "nvidia"
    monkeypatch.setattr(s, "anthropic_api_key", "sk-test")
    assert resolve_model("claude-sonnet-5-5") == ("anthropic", "claude-sonnet-5-5")


def test_tools_convert_to_openai_format_and_web_is_opt_in():
    names = [d["name"] for d in tool_definitions(web=False)]
    assert "search_web" not in names and "search_web" in [
        d["name"] for d in tool_definitions(web=True)
    ]
    fn = openai_tools(tool_definitions(web=True))[0]
    assert fn["type"] == "function" and fn["function"]["parameters"]["required"] == ["queries"]


def test_pagination_centres_on_the_found_passage():
    body = "a" * 20_000 + "NEEDLE" + "b" * 20_000
    window, page, pages = paginate(body, None, "NEEDLE")
    assert pages == 3 and page == 2 and "NEEDLE" in window
    assert paginate(body, 99, None)[1] == 3
    assert paginate("short", None, None) == ("short", 1, 1)


def test_rate_limiter_blocks_after_the_limit_and_resets():
    limiter = RateLimiter(2, 60)
    assert limiter.retry_after("k") == 0 and limiter.retry_after("k") == 0
    assert limiter.retry_after("k") > 0
    limiter.reset("k")
    assert limiter.retry_after("k") == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/health",
        "http://localhost/",
        "file:///etc/passwd",
        "http://10.0.0.5/",
    ],
)
async def test_page_fetcher_refuses_private_addresses(url):
    with pytest.raises(WebSearchError):
        await fetch_page(url)


def test_gateway_key_alone_is_not_a_chat_model(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", "")
    monkeypatch.setattr(s, "nvidia_api_key", "")
    monkeypatch.setattr(s, "explabs_api_key", "xpl-test")
    assert s.llm_providers == ["explabs"] and not s.llm_configured
    assert resolve_model("explabs:gpt-5.4-nano") == ("explabs", "gpt-5.4-nano")
    with pytest.raises(LLMUnavailable):
        resolve_model("claude-opus-5-5")


def test_unavailable_model_falls_back_to_configured_default(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", "")
    monkeypatch.setattr(s, "nvidia_api_key", "nv-test")
    monkeypatch.setattr(s, "explabs_api_key", "xpl-test")
    monkeypatch.setattr(s, "llm_model", "explabs:claude-haiku-5.5")
    assert resolve_model("claude-opus-5-5") == ("explabs", "claude-haiku-5.5")
    monkeypatch.setattr(s, "llm_model", "")
    assert resolve_model("claude-opus-5-5")[0] == "nvidia"


def test_list_arguments_accept_a_bare_string():
    from app.agent.tools import ToolBox, ToolInputError

    assert ToolBox._str_list("HIPAA deadline", "queries", required=True) == ["HIPAA deadline"]
    assert ToolBox._str_list([" a ", "b"], "queries") == ["a", "b"]
    with pytest.raises(ToolInputError):
        ToolBox._str_list(42, "queries", required=True)
    with pytest.raises(ToolInputError):
        ToolBox._str_list("   ", "queries", required=True)


def test_weak_secret_key_is_refused_over_https():
    from app.core.config import DEV_SECRET_KEY, Settings

    for weak in (DEV_SECRET_KEY, "short-key"):
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(secret_key=weak, cookie_secure=True)
    assert Settings(secret_key=DEV_SECRET_KEY, cookie_secure=False).secret_key == DEV_SECRET_KEY
    assert Settings(secret_key="k" * 40, cookie_secure=True).cookie_secure
