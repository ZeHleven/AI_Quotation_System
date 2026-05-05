"""RAG 检索效果评测服务

评测逻辑从 eval_rag.py 提取，避免跨目录 import。
后台线程执行，不阻塞热更新响应。
"""
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.core.config import settings

# ── 内嵌测试集（与 eval_rag.py 保持同步）──
TEST_CASES = [
    {"level": 1, "query": "直线型吊顶",           "expected": ["直线型吊顶"]},
    {"level": 1, "query": "地砖铺贴",              "expected": ["地砖铺贴"]},
    {"level": 1, "query": "水路改造",              "expected": ["水路改造"]},
    {"level": 1, "query": "电路改造",              "expected": ["电路改造"]},
    {"level": 1, "query": "卫生间防水",            "expected": ["卫生间防水"]},
    {"level": 1, "query": "乳胶漆涂刷",            "expected": ["乳胶漆涂刷"]},
    {"level": 1, "query": "铲墙皮",                "expected": ["铲墙皮"]},
    {"level": 1, "query": "地暖铺设",              "expected": ["地暖铺设"]},
    {"level": 2, "query": "铺地砖多少钱",          "expected": ["地砖铺贴"]},
    {"level": 2, "query": "刷墙漆",                "expected": ["乳胶漆涂刷"]},
    {"level": 2, "query": "水管改造",              "expected": ["水路改造"]},
    {"level": 2, "query": "走线布线",              "expected": ["电路改造"]},
    {"level": 2, "query": "厨卫铝扣板天花板",      "expected": ["铝扣板吊顶"]},
    {"level": 2, "query": "拆旧瓷砖",              "expected": ["旧墙砖拆除", "地面砖拆除"]},
    {"level": 2, "query": "坐便器安装",            "expected": ["马桶安装"]},
    {"level": 2, "query": "做隔墙",                "expected": ["轻钢龙骨隔墙", "砌筑墙体"]},
    {"level": 3, "query": "客厅吊顶和地砖",        "expected": ["直线型吊顶", "地砖铺贴"]},
    {"level": 3, "query": "水电改造",              "expected": ["水路改造", "电路改造"]},
    {"level": 3, "query": "卫生间防水和铺墙砖",    "expected": ["卫生间防水", "墙面瓷砖铺贴"]},
    {"level": 3, "query": "拆墙、砌墙、刷漆",      "expected": ["拆除墙体", "砌筑墙体", "乳胶漆涂刷"]},
    {"level": 3, "query": "马桶、洗手盆、淋浴房安装", "expected": ["马桶安装", "洗手盆安装", "淋浴房安装"]},
    {"level": 4, "query": "轻钢龙骨石膏板天花",    "expected": ["直线型吊顶"]},
    {"level": 4, "query": "PPR管热熔连接",          "expected": ["水路改造"]},
    {"level": 4, "query": "BV2.5铜芯线穿PVC管",    "expected": ["电路改造"]},
    {"level": 4, "query": "东方雨虹防水涂料刷墙",   "expected": ["墙面防水处理", "卫生间防水"]},
    {"level": 4, "query": "自流平水泥",             "expected": ["地面找平"]},
    {"level": 4, "query": "PE-RT管分集水器",        "expected": ["地暖铺设"]},
    {"level": 4, "query": "耐水腻子打磨阴阳角",     "expected": ["墙面腻子批刮"]},
    {"level": 4, "query": "加气混凝土砌块室内隔断", "expected": ["砌筑墙体"]},
]

_eval_lock = threading.Lock()
_current_report_id: int | None = None


def call_rag(url: str, query: str, top_k: int) -> list | None:
    try:
        resp = requests.post(
            f"{url}/api/v1/retrieve",
            json={"query": query, "top_k": top_k},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [item.get("item_name", "") for item in data]
    except requests.exceptions.ConnectionError:
        return None
    except Exception:
        return []


def hit_at_k(returned: list, expected: list) -> bool:
    return any(
        any(exp in item or item in exp for exp in expected)
        for item in returned
    )


def reciprocal_rank(returned: list, expected: list) -> float:
    for rank, item in enumerate(returned, start=1):
        if any(exp in item or item in exp for exp in expected):
            return 1.0 / rank
    return 0.0


def _run_eval_core(rag_url: str, top_k: int) -> dict:
    """执行评测并返回结果字典，不打印、不写文件。"""
    results = []
    level_stats: dict = {1: [], 2: [], 3: [], 4: []}

    for case in TEST_CASES:
        returned = call_rag(rag_url, case["query"], top_k)
        if returned is None:
            raise RuntimeError(f"RAG 服务不可达: {rag_url}")
        hit = hit_at_k(returned, case["expected"])
        rr = reciprocal_rank(returned, case["expected"])
        record = {**case, "returned": returned, "hit": hit, "rr": rr}
        results.append(record)
        level_stats[case["level"]].append(record)
        time.sleep(0.3)

    total = len(results)
    total_hit = sum(r["hit"] for r in results)
    total_mrr = sum(r["rr"] for r in results) / total

    by_level = {
        lv: {
            "hit_rate": round(sum(r["hit"] for r in level_stats[lv]) / len(level_stats[lv]), 4),
            "mrr": round(sum(r["rr"] for r in level_stats[lv]) / len(level_stats[lv]), 4),
        }
        for lv in [1, 2, 3, 4]
    }

    return {
        "timestamp": datetime.now().isoformat(),
        "url": rag_url,
        "top_k": top_k,
        "case_count": total,
        "hit_rate": round(total_hit / total, 4),
        "mrr": round(total_mrr, 4),
        "by_level": by_level,
        "cases": results,
    }


def _run_eval_thread(report_id: int, db_session_factory) -> None:
    global _current_report_id
    from app.models.rag_eval_report import RagEvalReport

    db = db_session_factory()
    try:
        report_data = _run_eval_core(settings.rag_service_url, settings.rag_eval_top_k)

        output_dir = Path(settings.materials_file).parent / "rag_eval_reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = output_dir / f"rag_eval_{timestamp}.json"
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        report = db.query(RagEvalReport).filter(RagEvalReport.id == report_id).first()
        if report:
            report.status = "completed"
            report.finished_at = datetime.now(timezone.utc)
            report.case_count = report_data["case_count"]
            report.hit_rate = report_data["hit_rate"]
            report.mrr = report_data["mrr"]
            report.by_level_json = json.dumps(report_data["by_level"], ensure_ascii=False)
            report.report_path = str(report_path)
            db.commit()
    except Exception as exc:
        report = db.query(RagEvalReport).filter(RagEvalReport.id == report_id).first()
        if report:
            report.status = "failed"
            report.finished_at = datetime.now(timezone.utc)
            report.error = str(exc)
            db.commit()
    finally:
        db.close()
        with _eval_lock:
            _current_report_id = None


def trigger_eval_background(triggered_by: str, db_session_factory) -> int:
    """
    在后台线程启动 RAG 评测。若已有评测在运行则返回当前的 report_id。
    返回值：新建或正在运行的 RagEvalReport.id。
    """
    global _current_report_id
    from app.models.rag_eval_report import RagEvalReport

    with _eval_lock:
        if _current_report_id is not None:
            return _current_report_id

        db = db_session_factory()
        try:
            report = RagEvalReport(
                triggered_by=triggered_by,
                status="running",
                started_at=datetime.now(timezone.utc),
                top_k=settings.rag_eval_top_k,
            )
            db.add(report)
            db.commit()
            db.refresh(report)
            report_id = report.id
        finally:
            db.close()

        _current_report_id = report_id

    thread = threading.Thread(
        target=_run_eval_thread,
        args=(report_id, db_session_factory),
        daemon=True,
    )
    thread.start()
    return report_id
