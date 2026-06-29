from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.services.drawing_ocr_llm_cabinet_classifier import (
    build_ocr_llm_cabinet,
    classify_batch_with_llm,
    select_context_packages,
    _sample_bucket,
)


def test_ocr_llm_cabinet_keeps_one_classification_per_evidence(tmp_path: Path) -> None:
    packages = [
        _package("T001", "墙刷白色无机涂料三度", nearby=[("T002", "+50mm黑色拉丝不锈钢踢脚线")]),
        _package("T002", "GL-01", nearby=[("T003", "10mm钢化磨砂玻璃")]),
        _package("T003", "300x600", nearby=[("T004", "拆除墙面300x600墙面砖")]),
        _package("T004", ".", nearby=[("T001", "墙刷白色无机涂料三度")]),
    ]

    async def fake_classifier(batch_payload: dict) -> dict:
        classifications = []
        for item in batch_payload["input_items"]:
            text = item["current_text"]
            if text == ".":
                classifications.append(
                    {
                        "text_id": item["text_id"],
                        "current_text": text,
                        "primary_category": "噪声",
                        "secondary_category": "纯符号",
                        "is_effective": False,
                        "confidence": 0.95,
                        "reason": "当前是孤立标点符号，没有业务语义。",
                        "related_text_ids": [],
                        "needs_vlm_review": False,
                        "vlm_review_reason": "",
                        "noise_reason": "孤立符号",
                        "suggested_usage": [],
                    }
                )
            elif text == "GL-01":
                classifications.append(
                    {
                        "text_id": item["text_id"],
                        "current_text": text,
                        "primary_category": "轴号/索引/编号",
                        "secondary_category": "材料代号",
                        "is_effective": True,
                        "confidence": 0.88,
                        "reason": "短文本像玻璃材料代号，周边出现钢化磨砂玻璃，不能作为噪声删除。",
                        "related_text_ids": ["T003"],
                        "needs_vlm_review": True,
                        "vlm_review_reason": "需要看材料表或图例确认 GL-01 的对应材料。",
                        "noise_reason": "",
                        "suggested_usage": ["材料代号"],
                    }
                )
            else:
                classifications.append(
                    {
                        "text_id": item["text_id"],
                        "current_text": text,
                        "primary_category": "材料/做法" if "涂料" in text else "规格尺寸",
                        "secondary_category": "",
                        "is_effective": True,
                        "confidence": 0.8,
                        "reason": "当前文字具备报价识别价值。",
                        "related_text_ids": [],
                        "needs_vlm_review": False,
                        "vlm_review_reason": "",
                        "noise_reason": "",
                        "suggested_usage": ["项目特征"],
                    }
                )
        return {"classifications": classifications, "raw_content": "fake"}

    report = asyncio.run(
        build_ocr_llm_cabinet(
            context_packages=packages,
            output_dir=tmp_path / "cabinet",
            sample_size=0,
            execute=True,
            classifier=fake_classifier,
        )
    )

    assert report["summary"]["classification_count"] == 4
    by_id = {row["text_id"]: row for row in report["classifications"]}
    assert by_id["T001"]["current_text"] == "墙刷白色无机涂料三度"
    assert by_id["T001"]["primary_category"] == "材料/做法"
    assert by_id["T002"]["primary_category"] == "轴号/索引/编号"
    assert by_id["T002"]["is_effective"] is True
    assert by_id["T004"]["primary_category"] == "噪声"
    assert by_id["T004"]["is_effective"] is False

    classified_json = Path(report["outputs"]["classified_ocr_cabinet_json"])
    classified_csv = Path(report["outputs"]["classified_ocr_cabinet_csv"])
    review_md = Path(report["outputs"]["classified_ocr_cabinet_review_md"])
    assert classified_json.exists()
    assert classified_csv.exists()
    assert review_md.exists()
    saved = json.loads(classified_json.read_text(encoding="utf-8"))
    assert len(saved["classifications"]) == 4
    assert "中文判断原因" in classified_csv.read_text(encoding="utf-8-sig")


def test_ocr_llm_cabinet_fills_missing_llm_rows_as_uncertain(tmp_path: Path) -> None:
    packages = [
        _package("T001", "拆除墙面300x600墙面砖"),
        _package("T002", "10mm钢化磨砂玻璃"),
    ]

    async def incomplete_classifier(batch_payload: dict) -> dict:
        first = batch_payload["input_items"][0]
        return {
            "classifications": [
                {
                    "text_id": first["text_id"],
                    "current_text": first["current_text"],
                    "primary_category": "拆除项",
                    "is_effective": True,
                    "confidence": 0.9,
                    "reason": "当前文字包含拆除语义。",
                }
            ]
        }

    report = asyncio.run(
        build_ocr_llm_cabinet(
            context_packages=packages,
            output_dir=tmp_path / "cabinet",
            sample_size=0,
            execute=True,
            classifier=incomplete_classifier,
        )
    )

    by_id = {row["text_id"]: row for row in report["classifications"]}
    assert by_id["T001"]["primary_category"] == "拆除项"
    assert by_id["T002"]["primary_category"] == "不确定"
    assert by_id["T002"]["needs_vlm_review"] is True
    assert "未返回" in by_id["T002"]["reason"]


def test_ocr_llm_cabinet_dry_run_does_not_require_model_key(tmp_path: Path) -> None:
    report = asyncio.run(
        build_ocr_llm_cabinet(
            context_packages=[_package("T001", "墙刷白色无机涂料三度")],
            output_dir=tmp_path / "cabinet",
            sample_size=1,
            execute=False,
            provider="deepseek",
        )
    )

    assert report["summary"]["mode"] == "dry_run"
    assert report["classifications"] == []
    assert Path(report["outputs"]["llm_cabinet_batches_preview_json"]).exists()
    assert Path(report["outputs"]["llm_cabinet_prompt_md"]).exists()


def test_external_minimal_payload_omits_paths_but_keeps_local_review_metadata(tmp_path: Path) -> None:
    captured_payloads: list[dict] = []

    async def fake_classifier(batch_payload: dict) -> dict:
        captured_payloads.append(batch_payload)
        return {
            "classifications": [
                {
                    "text_id": item["text_id"],
                    "current_text": item["current_text"],
                    "primary_category": "材料/做法",
                    "is_effective": True,
                    "confidence": 0.8,
                    "reason": "当前文字具备材料做法语义。",
                }
                for item in batch_payload["input_items"]
            ]
        }

    report = asyncio.run(
        build_ocr_llm_cabinet(
            context_packages=[_package("T001", "某某建设有限公司", nearby=[("T002", "审核")])],
            output_dir=tmp_path / "cabinet",
            sample_size=0,
            execute=True,
            classifier=fake_classifier,
            external_payload_mode="minimal",
            mask_sensitive_text=True,
        )
    )

    sent_item = captured_payloads[0]["input_items"][0]
    assert sent_item["current_text"] == "[图签公司/人员/证书信息]"
    assert "image_path" not in sent_item
    assert "bbox_ratio" not in sent_item
    assert "tile_id" not in sent_item
    assert set(sent_item["nearby_evidences"][0]) == {"text_id", "text", "relation", "rank"}
    classification = report["classifications"][0]
    assert classification["current_text"] == "某某建设有限公司"
    assert classification["image_path"] == "C:/tmp/T001.png"


def test_resume_skips_completed_partial_classifications(tmp_path: Path) -> None:
    output_dir = tmp_path / "cabinet"
    output_dir.mkdir()
    partial = {
        "classifications": [
            {
                "text_id": "T001",
                "current_text": "防水石膏板刷白色防潮无机涂料",
                "primary_category": "材料/做法",
                "is_effective": True,
                "confidence": 1.0,
                "reason": "已有结果",
            },
            {
                "text_id": "T002",
                "current_text": "成品木饰面",
                "primary_category": "材料/做法",
                "is_effective": True,
                "confidence": 1.0,
                "reason": "已有结果",
            },
        ]
    }
    (output_dir / "classified_ocr_cabinet.partial.json").write_text(json.dumps(partial, ensure_ascii=False), encoding="utf-8")
    calls: list[list[str]] = []

    async def fake_classifier(batch_payload: dict) -> dict:
        ids = [item["text_id"] for item in batch_payload["input_items"]]
        calls.append(ids)
        return {
            "classifications": [
                {
                    "text_id": item["text_id"],
                    "current_text": item["current_text"],
                    "primary_category": "拆除项",
                    "is_effective": True,
                    "confidence": 0.9,
                    "reason": "补跑结果",
                }
                for item in batch_payload["input_items"]
            ]
        }

    report = asyncio.run(
        build_ocr_llm_cabinet(
            context_packages=[
                _package("T001", "防水石膏板刷白色防潮无机涂料"),
                _package("T002", "成品木饰面"),
                _package("T003", "拆除实木门"),
            ],
            output_dir=output_dir,
            sample_size=0,
            max_items_per_batch=1,
            execute=True,
            resume=True,
            classifier=fake_classifier,
        )
    )

    assert calls == [["T003"]]
    assert report["summary"]["classification_count"] == 3
    by_id = {row["text_id"]: row for row in report["classifications"]}
    assert by_id["T001"]["reason"] == "已有结果"
    assert by_id["T003"]["reason"] == "补跑结果"


def test_representative_sampler_keeps_short_codes_and_noise_examples() -> None:
    packages = [
        _package("T001", "墙刷白色无机涂料三度"),
        _package("T002", "GL-01"),
        _package("T003", "."),
        _package("T004", "拆除墙面300x600墙面砖"),
        _package("T005", "设计说明"),
    ]

    selected = select_context_packages(packages, sample_size=4, strategy="representative")
    selected_ids = {row["text_id"] for row in selected}
    assert "T002" in selected_ids
    assert "T003" in selected_ids
    assert len(selected) == 4


def test_sampler_does_not_let_nearby_text_override_fragment_bucket() -> None:
    package = _package("T001", "一", nearby=[("T002", "一、拆除不锈钢玻璃地弹门")])

    assert _sample_bucket(package) == "明显噪声线索"


def test_local_provider_rejects_public_endpoint() -> None:
    batch_payload = {
        "input_items": [
            {
                "text_id": "T001",
                "current_text": "墙刷白色无机涂料三度",
                "nearby_evidences": [],
            }
        ]
    }

    try:
        asyncio.run(
            classify_batch_with_llm(
                batch_payload,
                provider="local",
                model="local-model",
                local_chat_url="https://api.deepseek.com/chat/completions",
            )
        )
    except RuntimeError as exc:
        assert "localhost or private intranet" in str(exc)
    else:
        raise AssertionError("provider=local must reject public endpoints")


def _package(text_id: str, text: str, *, nearby: list[tuple[str, str]] | None = None) -> dict[str, object]:
    nearby_rows = [
        {
            "rank": index,
            "text_id": nearby_id,
            "text": nearby_text,
            "relation": "same_tile",
            "dx_ratio": 0.01 * index,
            "dy_ratio": 0.002,
            "page_distance_ratio": 0.01 * index,
            "tile_id": "p001_r001_c001",
        }
        for index, (nearby_id, nearby_text) in enumerate(nearby or [], start=1)
    ]
    return {
        "schema_version": "drawing_ocr_context_package_v1",
        "text_id": text_id,
        "source_file": "drawing.pdf",
        "page": 1,
        "current_text": text,
        "confidence": 0.99,
        "bbox_ratio": [0.1, 0.1, 0.2, 0.12],
        "tile_id": "p001_r001_c001",
        "snippet_id": f"{text_id}_snippet",
        "image_path": f"C:/tmp/{text_id}.png",
        "current_features": {
            "text_length": len(text),
            "is_single_char": len(text) == 1,
            "has_chinese": True,
            "has_number": any(char.isdigit() for char in text),
            "has_dimension_pattern": "300x600" in text or "10mm" in text,
            "page_zone": "middle_center",
        },
        "nearby_text_ids": [row["text_id"] for row in nearby_rows],
        "nearby_texts": [row["text"] for row in nearby_rows],
        "nearby_evidences": nearby_rows,
        "neighborhood_stats": {"nearby_count": len(nearby_rows)},
        "llm_context_text": text,
    }
