"""Tests for the viral archetypes library."""

import pytest

from mobile_e2e.web import archetypes
from mobile_e2e.web.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_list_archetypes_flattened_ru_and_en():
    ru = archetypes.list_archetypes("ru")
    en = archetypes.list_archetypes("en")
    assert len(ru) == len(en) == len(archetypes.ARCHETYPES)
    a = ru[0]
    assert {"id", "name", "audience", "metric", "engine", "guidance", "examples"} <= a.keys()
    assert isinstance(a["name"], str) and isinstance(a["examples"], list)
    # a known viral mechanic is present
    assert any(x["id"] == "chain_question_men" for x in ru)


def test_reply_chain_is_the_dominant_metric():
    # The library must encode that comments drive reach (most formats target replies).
    metrics = [a["metric"] for a in archetypes.list_archetypes()]
    assert metrics.count("replies") >= 3


def test_get_archetype_and_missing():
    assert archetypes.get_archetype("participatory")["audience"] == "both"
    assert archetypes.get_archetype("nope") is None


def test_viral_formats_text_mentions_reply_chain():
    txt = archetypes.viral_formats_text("ru")
    assert "КОММЕНТ" in txt.upper() or "REPLY" in txt.upper()
    assert "chain_question_men" not in txt  # renders names, not raw ids
    en = archetypes.viral_formats_text("en")
    assert "REPLY CHAIN" in en.upper()


def test_seed_prompts_creates_once(store):
    created = archetypes.seed_prompts(store)
    assert created == len(archetypes.ARCHETYPES)
    names = [p["name"] for p in store.list_prompts()]
    assert all(n.startswith("🔥") for n in names)
    # idempotent: a second seed adds nothing
    assert archetypes.seed_prompts(store) == 0
