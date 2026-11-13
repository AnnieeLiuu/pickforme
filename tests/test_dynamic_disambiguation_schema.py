# -*- coding: utf-8 -*-
import json

from src.agent.schemas import IntentOutput, SubAreaOption


def test_sub_area_option_defaults():
    opt = SubAreaOption(name="熱海溫泉街")
    assert opt.name == "熱海溫泉街"
    assert opt.desc == ""
    assert opt.search_loc is None

    opt_full = SubAreaOption(
        name="修善寺溫泉街",
        desc="百年古湯、竹林",
        search_loc="修善寺",
    )
    assert opt_full.name == "修善寺溫泉街"
    assert opt_full.desc == "百年古湯、竹林"
    assert opt_full.search_loc == "修善寺"


def test_intent_output_defaults():
    output = IntentOutput(intent="search", location="新宿")
    assert output.is_location_ambiguous is False
    assert output.ambiguity_reason is None
    assert output.suggested_sub_areas == []


def test_intent_output_validation_ambiguous_location():
    raw_json = json.dumps(
        {
            "intent": "search",
            "location": "靜岡溫泉街",
            "cuisine": "熱門人氣美食",
            "is_location_ambiguous": True,
            "ambiguity_reason": "靜岡境內無名為靜岡溫泉街之單一地標，主要溫泉區為熱海、修善寺、伊東",
            "suggested_sub_areas": [
                {
                    "name": "熱海溫泉街",
                    "desc": "海景、溫泉布丁",
                    "search_loc": "熱海溫泉",
                },
                {
                    "name": "修善寺溫泉街",
                    "desc": "百年古湯、竹林",
                    "search_loc": "修善寺",
                },
            ],
        }
    )

    parsed = IntentOutput.model_validate_json(raw_json)
    assert parsed.intent == "search"
    assert parsed.location == "靜岡溫泉街"
    assert parsed.is_location_ambiguous is True
    assert parsed.ambiguity_reason == "靜岡境內無名為靜岡溫泉街之單一地標，主要溫泉區為熱海、修善寺、伊東"
    assert len(parsed.suggested_sub_areas) == 2
    assert parsed.suggested_sub_areas[0].name == "熱海溫泉街"
    assert parsed.suggested_sub_areas[0].desc == "海景、溫泉布丁"
    assert parsed.suggested_sub_areas[0].search_loc == "熱海溫泉"
    assert parsed.suggested_sub_areas[1].name == "修善寺溫泉街"
    assert parsed.suggested_sub_areas[1].search_loc == "修善寺"


def test_intent_output_validation_non_ambiguous_landmark():
    raw_json = json.dumps(
        {
            "intent": "search",
            "location": "箱根湯本",
            "cuisine": "豆腐排煮",
            "is_location_ambiguous": False,
            "suggested_sub_areas": [],
        }
    )

    parsed = IntentOutput.model_validate_json(raw_json)
    assert parsed.intent == "search"
    assert parsed.location == "箱根湯本"
    assert parsed.cuisine == "豆腐排煮"
    assert parsed.is_location_ambiguous is False
    assert parsed.suggested_sub_areas == []


def test_intent_output_schema_prompt_generation():
    prompt = IntentOutput.build_json_schema_prompt()
    assert '"is_location_ambiguous"' in prompt
    assert '"suggested_sub_areas"' in prompt
