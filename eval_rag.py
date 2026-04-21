"""
RAG 检索效果评测脚本
用法: python eval_rag.py [--url http://192.168.88.128:8001] [--top_k 5]

指标说明:
  Hit@K  — 前 K 个结果中至少命中一个期望项目的查询比例
  MRR    — 平均倒数排名，衡量期望结果排在第几位（越靠前越好，满分=1.0）
  各查询的 Pass/Fail 明细

测试集设计原则（共 30 条）:
  Level-1 直接命名  — 用 item_name 原词查询，基线应接近满分
  Level-2 同义口语  — 用业务员口语描述，测试语义泛化能力
  Level-3 多意图    — 一句话含多个施工项目，测试拆分召回能力
  Level-4 描述性    — 只描述工艺/材料，不提项目名，测试深度语义理解
"""

import argparse
import json
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    print("缺少 requests 库，请运行: pip install requests")
    sys.exit(1)

# ──────────────────────────────────────────────
# 评测数据集（query → 期望命中的 item_name 列表）
# ──────────────────────────────────────────────
TEST_CASES = [
    # ── Level-1: 直接命名（应全部命中）──
    {"level": 1, "query": "直线型吊顶",           "expected": ["直线型吊顶"]},
    {"level": 1, "query": "地砖铺贴",              "expected": ["地砖铺贴"]},
    {"level": 1, "query": "水路改造",              "expected": ["水路改造"]},
    {"level": 1, "query": "电路改造",              "expected": ["电路改造"]},
    {"level": 1, "query": "卫生间防水",            "expected": ["卫生间防水"]},
    {"level": 1, "query": "乳胶漆涂刷",            "expected": ["乳胶漆涂刷"]},
    {"level": 1, "query": "铲墙皮",                "expected": ["铲墙皮"]},
    {"level": 1, "query": "地暖铺设",              "expected": ["地暖铺设"]},

    # ── Level-2: 同义口语（测试语义泛化）──
    {"level": 2, "query": "铺地砖多少钱",          "expected": ["地砖铺贴"]},
    {"level": 2, "query": "刷墙漆",                "expected": ["乳胶漆涂刷"]},
    {"level": 2, "query": "水管改造",              "expected": ["水路改造"]},
    {"level": 2, "query": "走线布线",              "expected": ["电路改造"]},
    {"level": 2, "query": "厨卫铝扣板天花板",      "expected": ["铝扣板吊顶"]},
    {"level": 2, "query": "拆旧瓷砖",              "expected": ["旧墙砖拆除", "地面砖拆除"]},
    {"level": 2, "query": "坐便器安装",            "expected": ["马桶安装"]},
    {"level": 2, "query": "做隔墙",                "expected": ["轻钢龙骨隔墙", "砌筑墙体"]},

    # ── Level-3: 多意图拆分（一句含多项）──
    {"level": 3, "query": "客厅吊顶和地砖",        "expected": ["直线型吊顶", "地砖铺贴"]},
    {"level": 3, "query": "水电改造",              "expected": ["水路改造", "电路改造"]},
    {"level": 3, "query": "卫生间防水和铺墙砖",    "expected": ["卫生间防水", "墙面瓷砖铺贴"]},
    {"level": 3, "query": "拆墙、砌墙、刷漆",      "expected": ["拆除墙体", "砌筑墙体", "乳胶漆涂刷"]},
    {"level": 3, "query": "马桶、洗手盆、淋浴房安装", "expected": ["马桶安装", "洗手盆安装", "淋浴房安装"]},

    # ── Level-4: 描述性查询（只给材料/工艺描述）──
    {"level": 4, "query": "轻钢龙骨石膏板天花",    "expected": ["直线型吊顶"]},
    {"level": 4, "query": "PPR管热熔连接",          "expected": ["水路改造"]},
    {"level": 4, "query": "BV2.5铜芯线穿PVC管",    "expected": ["电路改造"]},
    {"level": 4, "query": "东方雨虹防水涂料刷墙",   "expected": ["墙面防水处理", "卫生间防水"]},
    {"level": 4, "query": "自流平水泥",             "expected": ["地面找平"]},
    {"level": 4, "query": "PE-RT管分集水器",        "expected": ["地暖铺设"]},
    {"level": 4, "query": "耐水腻子打磨阴阳角",     "expected": ["墙面腻子批刮"]},
    {"level": 4, "query": "加气混凝土砌块室内隔断", "expected": ["砌筑墙体"]},
]


def call_rag(url: str, query: str, top_k: int) -> list[str]:
    """调用 RAG 服务，返回 item_name 列表（按排名顺序）"""
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
        return None  # 服务不可达
    except Exception as e:
        print(f"  [ERROR] {e}")
        return []


def reciprocal_rank(returned: list[str], expected: list[str]) -> float:
    """计算单条查询的倒数排名（取期望列表中最高排名的那个）"""
    for rank, item in enumerate(returned, start=1):
        if any(exp in item or item in exp for exp in expected):
            return 1.0 / rank
    return 0.0


def hit_at_k(returned: list[str], expected: list[str]) -> bool:
    """判断 returned 列表中是否命中至少一个 expected"""
    for item in returned:
        if any(exp in item or item in exp for exp in expected):
            return True
    return False


def run_eval(url: str, top_k: int):
    print(f"\n{'='*60}")
    print(f"  RAG 检索效果评测  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  服务地址: {url}   top_k={top_k}")
    print(f"{'='*60}\n")

    # 先验证服务是否可达
    probe = call_rag(url, "测试", 1)
    if probe is None:
        print(f"[FATAL] 无法连接到 RAG 服务 {url}")
        print("  请先在 CentOS 执行: cd /opt/rag_service && docker compose up -d")
        sys.exit(1)

    results = []
    level_stats = {1: [], 2: [], 3: [], 4: []}

    for i, case in enumerate(TEST_CASES, 1):
        returned = call_rag(url, case["query"], top_k)
        if returned is None:
            print(f"[FATAL] 服务中途断连，评测中止")
            sys.exit(1)

        hit = hit_at_k(returned, case["expected"])
        rr = reciprocal_rank(returned, case["expected"])

        status = "PASS" if hit else "FAIL"
        marker = "✓" if hit else "✗"

        print(f"[{i:02d}] L{case['level']} {marker} MRR={rr:.2f}  |  {case['query'][:30]:<30}")
        if not hit:
            print(f"       期望: {case['expected']}")
            print(f"       实际: {returned[:3]}")

        record = {**case, "returned": returned, "hit": hit, "rr": rr}
        results.append(record)
        level_stats[case["level"]].append(record)

        time.sleep(0.3)  # 避免请求过于密集

    # ── 汇总指标 ──
    total = len(results)
    total_hit = sum(r["hit"] for r in results)
    total_mrr = sum(r["rr"] for r in results) / total

    print(f"\n{'='*60}")
    print(f"  汇总结果")
    print(f"{'='*60}")
    print(f"  总查询数:   {total}")
    print(f"  Hit@{top_k}:     {total_hit}/{total}  ({total_hit/total*100:.1f}%)")
    print(f"  MRR:        {total_mrr:.4f}")

    print(f"\n  按难度级别分解:")
    level_names = {1: "L1 直接命名", 2: "L2 同义口语", 3: "L3 多意图", 4: "L4 描述性"}
    for lv in [1, 2, 3, 4]:
        lv_records = level_stats[lv]
        lv_hit = sum(r["hit"] for r in lv_records)
        lv_mrr = sum(r["rr"] for r in lv_records) / len(lv_records) if lv_records else 0
        print(f"    {level_names[lv]:<14}  Hit={lv_hit}/{len(lv_records)} ({lv_hit/len(lv_records)*100:.0f}%)  MRR={lv_mrr:.3f}")

    print(f"{'='*60}\n")

    # ── 保存结果 ──
    report = {
        "timestamp": datetime.now().isoformat(),
        "url": url,
        "top_k": top_k,
        "hit_rate": round(total_hit / total, 4),
        "mrr": round(total_mrr, 4),
        "by_level": {
            lv: {
                "hit_rate": round(sum(r["hit"] for r in level_stats[lv]) / len(level_stats[lv]), 4),
                "mrr": round(sum(r["rr"] for r in level_stats[lv]) / len(level_stats[lv]), 4),
            }
            for lv in [1, 2, 3, 4]
        },
        "cases": results,
    }

    output_path = f"rag_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  详细结果已保存至: {output_path}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 检索效果评测")
    parser.add_argument("--url", default="http://192.168.88.128:8001", help="RAG 服务地址")
    parser.add_argument("--top_k", type=int, default=5, help="检索返回条数")
    args = parser.parse_args()

    run_eval(args.url, args.top_k)
