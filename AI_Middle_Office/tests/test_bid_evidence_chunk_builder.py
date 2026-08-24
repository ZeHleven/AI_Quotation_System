from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.services.bid_evidence_chunk_builder import (
    BidEvidenceChunkBuildError,
    RQ1A_CHUNK_PROFILE,
    StructuredEvidenceBlock,
    build_evidence_chunks,
    estimate_evidence_tokens,
)


ROOT = Path(__file__).resolve().parents[1]


def _block(
    key: str,
    text: str,
    *,
    ordinal: int,
    page_no: int = 1,
    block_type: str = "paragraph",
    section_path: tuple[str, ...] = ("第一章", "投标人须知"),
    boundary_before: bool = False,
    boundary_after: bool = False,
) -> StructuredEvidenceBlock:
    return StructuredEvidenceBlock(
        block_key=key,
        text=text,
        block_type=block_type,
        page_no=page_no,
        ordinal=ordinal,
        section_path=section_path,
        bbox=(10.0, 20.0 + ordinal, 500.0, 40.0 + ordinal),
        boundary_before=boundary_before,
        boundary_after=boundary_after,
    )


def _roles(result, role: str):
    return [item for item in result.fragments if item.fragment_role == role]


def test_pdf_c1_machine_contract_and_result_schema() -> None:
    profile = json.loads(
        (ROOT / "contracts/bid_assessment/v1/pdf-c1-chunk-profile.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile["contract_version"] == "bid.evidence.chunk.v2"
    assert profile["limits"] == {
        "soft_min_tokens": 220,
        "target_tokens": 380,
        "soft_max_tokens": 500,
        "hard_max_tokens": 600,
        "long_overlap_tokens": 80,
    }

    schema = json.loads(
        (ROOT / "schemas/bid_assessment/v1/evidence-chunk.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    result = build_evidence_chunks(
        [_block("p1", "投" * 230, ordinal=0)],
        document_label="合成招标文件",
    )
    Draft202012Validator(schema).validate(result.to_payload())


def test_token_estimator_is_deterministic_and_conservative_for_cjk() -> None:
    text = "招标ABCDEF 20%"
    assert estimate_evidence_tokens(text) == 6
    assert estimate_evidence_tokens(text) == estimate_evidence_tokens(text)
    assert estimate_evidence_tokens("投标保证金") == 5


def test_short_paragraphs_form_one_parent_child_with_citable_atoms() -> None:
    result = build_evidence_chunks(
        [
            _block("p1", "甲" * 120, ordinal=0),
            _block("p2", "乙" * 130, ordinal=1, page_no=2),
        ],
        document_label="合成招标文件",
    )
    parents = _roles(result, "section_parent")
    children = _roles(result, "retrieval_child")
    atoms = _roles(result, "evidence_atom")
    assert len(parents) == 1
    assert len(children) == 1
    assert len(atoms) == 2
    assert children[0].parent_key == parents[0].evidence_key
    assert {row.parent_key for row in atoms} == {children[0].evidence_key}
    assert not parents[0].is_citable
    assert not children[0].is_citable
    assert all(row.is_citable for row in atoms)
    assert children[0].locator["page_no"] == 1
    assert children[0].locator["page_end"] == 2
    assert "[章节] 第一章 > 投标人须知" in children[0].retrieval_text
    assert "[章节]" not in children[0].normalized_text
    assert result.metrics["overlap_estimated_tokens"] == 0


def test_section_change_is_hard_boundary_even_when_chunks_are_small() -> None:
    result = build_evidence_chunks(
        [
            _block(
                "p1",
                "甲" * 230,
                ordinal=0,
                section_path=("第一章", "投标人须知"),
            ),
            _block(
                "p2",
                "乙" * 230,
                ordinal=1,
                section_path=("第二章", "合同条件"),
            ),
        ]
    )
    parents = _roles(result, "section_parent")
    children = _roles(result, "retrieval_child")
    assert len(parents) == 2
    assert len(children) == 2
    assert children[0].parent_key != children[1].parent_key
    assert children[0].locator["section_path"] == ["第一章", "投标人须知"]
    assert children[1].locator["section_path"] == ["第二章", "合同条件"]


def test_clause_is_isolated_from_neighboring_body() -> None:
    result = build_evidence_chunks(
        [
            _block("p1", "正文" * 120, ordinal=0),
            _block(
                "c1",
                "3.1 投标保证金为人民币二十万元。",
                ordinal=1,
                block_type="clause",
            ),
            _block("p2", "后续" * 120, ordinal=2),
        ]
    )
    children = _roles(result, "retrieval_child")
    assert len(children) == 3
    clause = next(row for row in children if row.locator["block_type"] == "clause")
    assert clause.source_block_keys == ("c1",)
    assert "保证金" in clause.normalized_text


def test_table_is_an_isolated_child_with_one_citable_atom() -> None:
    result = build_evidence_chunks(
        [
            _block("p1", "正文" * 120, ordinal=0),
            _block(
                "table1",
                "项目名称 | 数量 | 单位\n防火门 | 12 | 樘",
                ordinal=1,
                block_type="table",
            ),
            _block("p2", "后续" * 120, ordinal=2),
        ]
    )
    children = _roles(result, "retrieval_child")
    atoms = _roles(result, "evidence_atom")
    table_child = next(
        row for row in children if row.locator["block_type"] == "table"
    )
    table_atoms = [row for row in atoms if row.parent_key == table_child.evidence_key]
    assert len(children) == 3
    assert len(table_atoms) == 1
    assert table_atoms[0].source_block_keys == ("table1",)
    assert table_atoms[0].is_citable


def test_long_block_uses_declared_overlap_without_exceeding_hard_limit() -> None:
    result = build_evidence_chunks(
        [_block("long", ("甲" * 499 + "。") * 3, ordinal=0)]
    )
    children = _roles(result, "retrieval_child")
    atoms = _roles(result, "evidence_atom")
    assert len(children) >= 3
    assert len(children) == len(atoms)
    assert max(row.estimated_tokens for row in children) <= 600
    assert all(row.locator["split_count"] == len(atoms) for row in atoms)
    assert atoms[0].locator["overlap_left_tokens"] == 0
    assert all(
        row.locator["overlap_left_tokens"] == 80 for row in atoms[1:]
    )
    assert result.metrics["overlap_estimated_tokens"] == 80 * (len(atoms) - 1)
    assert result.metrics["long_split_atom_count"] == len(atoms)


def test_atom_spans_cover_source_and_only_repeat_declared_overlap() -> None:
    source_text = ("甲" * 499 + "。") * 3
    result = build_evidence_chunks([_block("long", source_text, ordinal=0)])
    atoms = sorted(
        _roles(result, "evidence_atom"),
        key=lambda row: row.locator["split_index"],
    )
    assert atoms[0].locator["char_start"] == 0
    assert atoms[-1].locator["char_end"] == len(source_text)

    previous_end = 0
    for index, atom in enumerate(atoms):
        start = atom.locator["char_start"]
        end = atom.locator["char_end"]
        assert start <= previous_end
        if index == 0:
            assert start == 0
        else:
            repeated_text = source_text[start:previous_end]
            assert estimate_evidence_tokens(repeated_text) == atom.locator[
                "overlap_left_tokens"
            ]
        assert atom.normalized_text == source_text[start:end]
        previous_end = end


def test_same_input_and_profile_produce_stable_keys_and_result_hash() -> None:
    blocks = [
        _block("p1", "甲" * 230, ordinal=0),
        _block("p2", "乙" * 230, ordinal=1),
    ]
    first = build_evidence_chunks(blocks, document_label="稳定性样例")
    second = build_evidence_chunks(blocks, document_label="稳定性样例")
    assert first.result_hash == second.result_hash
    assert [row.evidence_key for row in first.fragments] == [
        row.evidence_key for row in second.fragments
    ]
    assert [row.locator_hash for row in first.fragments] == [
        row.locator_hash for row in second.fragments
    ]


def test_rq1a_rolls_up_micro_sections_and_makes_heading_sources_citable() -> None:
    blocks = [
        _block(
            "h1",
            "1.1 Commercial Area 1000 m2",
            ordinal=0,
            block_type="heading",
            section_path=("1 Scope", "1.1 Commercial"),
            boundary_before=True,
            boundary_after=True,
        ),
        _block(
            "p1",
            "A" * 80,
            ordinal=1,
            section_path=("1 Scope", "1.1 Commercial"),
        ),
        _block(
            "h2",
            "1.2 Office Area 1700 m2",
            ordinal=2,
            block_type="heading",
            section_path=("1 Scope", "1.2 Office"),
            boundary_before=True,
            boundary_after=True,
        ),
        _block(
            "p2",
            "B" * 80,
            ordinal=3,
            section_path=("1 Scope", "1.2 Office"),
        ),
    ]
    result = build_evidence_chunks(blocks, profile=RQ1A_CHUNK_PROFILE)
    parents = _roles(result, "section_parent")
    children = _roles(result, "retrieval_child")
    atoms = _roles(result, "evidence_atom")
    heading_atoms = [
        row for row in atoms if row.locator["block_type"] == "heading"
    ]

    assert len(parents) == 1
    assert len(children) == 1
    assert len(heading_atoms) == 2
    assert all(row.is_citable for row in heading_atoms)
    assert {row.normalized_text for row in heading_atoms} == {
        "1.1 Commercial Area 1000 m2",
        "1.2 Office Area 1700 m2",
    }
    assert {tuple(row.locator["section_path"]) for row in heading_atoms} == {
        ("1 Scope", "1.1 Commercial"),
        ("1 Scope", "1.2 Office"),
    }
    assert result.metrics["citable_heading_atom_count"] == 2
    assert result.metrics["parent_section_max_depth"] == 1


def test_rq1a_aggregates_vector_table_rows_but_preserves_row_atoms() -> None:
    blocks = [
        _block(
            "row1",
            "Duration | 45 days",
            ordinal=0,
            block_type="table_row",
            section_path=("2 Key Values",),
            boundary_before=True,
        ),
        _block(
            "row2",
            "Tax rate | 9%",
            ordinal=1,
            block_type="table_row",
            section_path=("2 Key Values",),
            boundary_after=True,
        ),
    ]
    result = build_evidence_chunks(blocks, profile=RQ1A_CHUNK_PROFILE)
    children = _roles(result, "retrieval_child")
    atoms = _roles(result, "evidence_atom")

    assert len(children) == 1
    assert children[0].locator["source_atom_count"] == 2
    assert {row.source_block_keys[0] for row in atoms} == {
        "row1",
        "row2",
    }
    assert result.metrics["aggregated_table_row_child_count"] == 1
    assert all(row.is_citable for row in atoms)


@pytest.mark.parametrize(
    "blocks,error_code",
    [
        ([], "BID_CHUNK_BLOCKS_EMPTY"),
        (
            [
                _block("duplicate", "甲" * 20, ordinal=0),
                _block("duplicate", "乙" * 20, ordinal=1),
            ],
            "BID_CHUNK_BLOCK_KEY_INVALID",
        ),
        (
            [
                _block("one", "甲" * 20, ordinal=0),
                _block("two", "乙" * 20, ordinal=0),
            ],
            "BID_CHUNK_BLOCK_ORDINAL_DUPLICATE",
        ),
    ],
)
def test_invalid_structure_fails_closed(blocks, error_code: str) -> None:
    with pytest.raises(BidEvidenceChunkBuildError, match=error_code):
        build_evidence_chunks(blocks)
