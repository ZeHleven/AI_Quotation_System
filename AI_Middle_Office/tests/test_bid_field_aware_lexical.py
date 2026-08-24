from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator

from app.services.bid_field_aware_lexical import (
    CHANNEL_ORDER,
    FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    LEXICAL_SEARCH_CONTRACT_VERSION,
    LexicalAtomSource,
    LexicalChildSource,
    build_field_aware_lexical_corpus,
    cache_field_aware_corpus,
    clear_field_aware_lexical_cache,
    get_cached_field_aware_corpus,
    rank_field_aware_bm25f,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    PROJECT_ROOT
    / "contracts"
    / "bid_assessment"
    / "v1"
    / "rq1d-field-aware-lexical-profile.json"
)
SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "bid_assessment"
    / "v1"
    / "lexical-search.schema.json"
)


def _source(
    child_id: str,
    *atoms: tuple[str, str],
    section_path: tuple[str, ...] = ("投标须知",),
) -> LexicalChildSource:
    return LexicalChildSource(
        child_id=child_id,
        child_key=f"key:{child_id}",
        entry_hash=(child_id[-1] * 64),
        child_text="\n".join(text for _kind, text in atoms),
        section_path=section_path,
        atoms=tuple(
            LexicalAtomSource(
                evidence_id=f"{child_id}-a{index}",
                text=text,
                block_type=kind,
                section_path=section_path,
            )
            for index, (kind, text) in enumerate(atoms, 1)
        ),
    )


def test_rq1d_machine_profile_and_projection_schema_are_frozen() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(profile)
    Draft202012Validator.check_schema(schema)
    assert profile["contract_version"] == LEXICAL_SEARCH_CONTRACT_VERSION
    assert profile["profile_version"] == FIELD_AWARE_LEXICAL_PROFILE_VERSION
    assert profile["projection"]["channels"] == list(CHANNEL_ORDER)
    assert profile["fusion"]["legacy_child_bm25_baseline_weight"] == 1.0
    assert profile["fusion"]["field_aware_bm25f_weak_weight"] == 0.005
    assert profile["fusion"]["field_aware_bm25f_structured_table_weight"] == 0.1
    assert profile["fusion"]["structured_table_channels"] == [
        "table_key",
        "table_value",
        "table_row",
    ]
    assert profile["fusion"]["field_aware_policy"] == (
        "bounded_structured_tie_breaker_not_baseline_replacement"
    )
    assert profile["fusion"]["original_query_anchor_source"] == (
        "legacy_child_bm25_q1"
    )
    assert profile["compatibility"]["new_database_migration_required"] is False
    validator = Draft202012Validator(schema)
    validator.validate(
        {
            "schema_version": LEXICAL_SEARCH_CONTRACT_VERSION,
            "profile_version": FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            "source_index_set_hash": "a" * 64,
            "projection_set_hash": "b" * 64,
            "projection_count": 3,
            "boilerplate_term_count": 2,
            "channels": list(CHANNEL_ORDER),
        }
    )


def test_projection_separates_heading_table_key_value_row_and_body() -> None:
    corpus = build_field_aware_lexical_corpus(
        [
            _source(
                "child1",
                ("heading", "3.2 投标文件递交"),
                ("table_row", "投标截止时间 | 2026年8月20日10时"),
                ("paragraph", "投标人应当在截止时间前完成递交。"),
            )
        ]
    )
    projection = corpus.projections[0]
    channels = projection.channel_map()
    assert "投标须知" in channels["section_heading"]
    assert "3.2 投标文件递交" in channels["section_heading"]
    assert channels["table_key"] == "投标截止时间"
    assert channels["table_value"] == "2026年8月20日10时"
    assert "投标截止时间 | 2026年8月20日10时" in channels["table_row"]
    assert "截止时间前完成递交" in channels["body"]
    assert len(projection.projection_hash) == 64


def test_uncertain_table_cells_fall_back_without_inventing_key_value() -> None:
    corpus = build_field_aware_lexical_corpus(
        [_source("child2", ("table_row", "100 | 200 | 300"))]
    )
    channels = corpus.projections[0].channel_map()
    assert channels["table_key"] == ""
    assert channels["table_value"] == ""
    assert channels["table_row"] == "100 | 200 | 300"


def test_projection_and_corpus_hashes_are_order_independent_and_stable() -> None:
    first = _source("child3", ("paragraph", "履约担保为中标价的10%。"))
    second = _source("child4", ("paragraph", "投标有效期为90日历天。"))
    left = build_field_aware_lexical_corpus([first, second])
    right = build_field_aware_lexical_corpus([second, first])
    assert left.corpus_hash == right.corpus_hash
    assert [row.child_id for row in left.projections] == ["child3", "child4"]


def test_projection_hash_uses_stable_evidence_key_not_database_uuid() -> None:
    source = _source("child3", ("paragraph", "履约担保为中标价的10%。"))
    rebased = replace(source, child_id="different-database-id")
    first = build_field_aware_lexical_corpus([source])
    second = build_field_aware_lexical_corpus([rebased])
    assert first.projections[0].projection_hash == (
        second.projections[0].projection_hash
    )
    assert first.corpus_hash == second.corpus_hash


def test_bm25f_uses_answer_shape_and_field_channels_deterministically() -> None:
    corpus = build_field_aware_lexical_corpus(
        [
            _source(
                "child5",
                ("table_row", "投标截止时间 | 2026年8月20日10时"),
            ),
            _source(
                "child6",
                ("paragraph", "关于投标截止时间的说明详见后续安排。"),
            ),
        ]
    )
    first = rank_field_aware_bm25f(
        "投标截止时间 年 月 日 时",
        corpus,
        field_codes=["submission.deadline"],
        answer_shapes=["datetime"],
    )
    second = rank_field_aware_bm25f(
        "投标截止时间 年 月 日 时",
        corpus,
        field_codes=["submission.deadline"],
        answer_shapes=["datetime"],
    )
    assert first == second
    assert first[0].child_id == "child5"
    assert "table_key" in first[0].matched_channels
    assert "table_value" in first[0].matched_channels


def test_document_frequency_marks_repeated_template_terms_without_deletion() -> None:
    corpus = build_field_aware_lexical_corpus(
        [
            _source(
                f"child{index}",
                ("paragraph", f"招标文件投标人通用条款 第{index}项"),
            )
            for index in range(7, 11)
        ]
    )
    assert "招标" in corpus.boilerplate_terms
    assert len(corpus.projections) == 4


def test_content_addressed_cache_revalidates_every_entry_hash() -> None:
    clear_field_aware_lexical_cache()
    corpus = build_field_aware_lexical_corpus(
        [_source("childa", ("paragraph", "质量标准为合格。"))]
    )
    cache_field_aware_corpus("cache-key", corpus)
    assert (
        get_cached_field_aware_corpus(
            "cache-key",
            expected_entry_hashes={"childa": "a" * 64},
        )
        == corpus
    )
    assert (
        get_cached_field_aware_corpus(
            "cache-key",
            expected_entry_hashes={"childa": "b" * 64},
        )
        is None
    )
