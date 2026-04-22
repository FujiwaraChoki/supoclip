"""Tests for the API pricing table."""

import pytest

from src.pricing import (
    LLM_PRICES,
    LLMPrice,
    assemblyai_cost_usd,
    get_llm_price,
    llm_cost_usd,
    llm_model_known,
)


class TestLlmCostUsd:
    def test_known_model_computes_linear_cost(self):
        price = LLM_PRICES["openai:gpt-4o-mini"]
        cost = llm_cost_usd("openai:gpt-4o-mini", 1_000_000, 2_000_000)
        expected = price.input_per_1m + 2 * price.output_per_1m
        assert cost == pytest.approx(expected)

    def test_unknown_model_returns_zero(self):
        assert llm_cost_usd("something:never-heard-of", 10_000, 5_000) == 0.0

    def test_case_insensitive_and_whitespace_tolerant(self):
        base = llm_cost_usd("openai:gpt-4o-mini", 100, 200)
        assert llm_cost_usd("  OpenAI:GPT-4o-MINI  ", 100, 200) == pytest.approx(base)

    def test_empty_model_is_zero(self):
        assert llm_cost_usd("", 100, 100) == 0.0
        assert llm_cost_usd(None, 100, 100) == 0.0  # type: ignore[arg-type]


class TestAssemblyaiCostUsd:
    def test_best_tier_default(self):
        assert assemblyai_cost_usd(3600, tier="best") == pytest.approx(0.37)

    def test_nano_tier(self):
        assert assemblyai_cost_usd(3600, tier="nano") == pytest.approx(0.12)

    def test_unknown_tier_falls_back_to_best(self):
        assert assemblyai_cost_usd(3600, tier="bogus") == pytest.approx(0.37)

    def test_case_insensitive(self):
        assert assemblyai_cost_usd(3600, tier="BEST") == pytest.approx(0.37)

    def test_prorates_by_seconds(self):
        assert assemblyai_cost_usd(1800, tier="best") == pytest.approx(0.185)


class TestLlmModelKnown:
    def test_known_true(self):
        assert llm_model_known("openai:gpt-4o-mini") is True

    def test_unknown_false(self):
        assert llm_model_known("fictional:model") is False

    def test_empty_false(self):
        assert llm_model_known("") is False


class TestGetLlmPrice:
    def test_returns_price_for_known(self):
        price = get_llm_price("anthropic:claude-opus-4-7")
        assert isinstance(price, LLMPrice)
        assert price.input_per_1m == 15.00
        assert price.output_per_1m == 75.00

    def test_returns_none_for_unknown(self):
        assert get_llm_price("fake:model") is None

    def test_none_input_is_none(self):
        assert get_llm_price(None) is None
        assert get_llm_price("") is None
