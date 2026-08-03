from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.n8n_quote_workflow_transform import (
    NoRagWorkflowTransformError,
    build_no_rag_quote_candidate,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从当前 n8n budget-calc 导出生成 inactive no-RAG 候选工作流。"
    )
    parser.add_argument("source", type=Path, help="当前线上 n8n 工作流导出 JSON")
    parser.add_argument("output", type=Path, help="候选工作流输出 JSON；文件可能继承源工作流中的敏感配置")
    parser.add_argument("--source-webhook-path", default="budget-calc")
    parser.add_argument("--candidate-webhook-path", default="budget-calc-no-rag")
    parser.add_argument("--name-suffix", default="【no-RAG候选】")
    args = parser.parse_args()

    if args.source.resolve() == args.output.resolve():
        parser.error("output 不能覆盖 source")

    source_bytes = args.source.read_bytes()
    try:
        exported_payload = json.loads(source_bytes.decode("utf-8-sig"))
        candidate, report = build_no_rag_quote_candidate(
            exported_payload,
            source_webhook_path=args.source_webhook_path,
            candidate_webhook_path=args.candidate_webhook_path,
            candidate_name_suffix=args.name_suffix,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, NoRagWorkflowTransformError) as exc:
        parser.error(str(exc))

    output_bytes = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output_bytes)

    summary = {
        "source_sha256": _sha256_bytes(source_bytes),
        "output_sha256": _sha256_bytes(output_bytes),
        "output": str(args.output.resolve()),
        "warning": "候选文件可能继承源工作流内联敏感配置，请勿提交或分享。",
        "transform": report.as_dict(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
