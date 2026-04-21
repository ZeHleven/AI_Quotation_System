import pandas as pd
import json
import os


def clean_data_to_rag_payload(input_path, output_jsonl_path):
    """
    极度客观的技术处理：强制实现语义与数值计算的解耦。
    输出格式变更为 JSONL，适配企业级向量数据库的 Metadata 挂载规范。
    """
    print(f"[System] 正在加载原始脏数据: {input_path} ...")
    try:
        df_raw = pd.read_csv(input_path, header=None, encoding='utf-8')
    except Exception as e:
        print(f"❌ 解析错误：{str(e)}")
        return

    # 动态表头嗅探
    header_idx = -1
    for i, row in df_raw.iterrows():
        row_values = [str(val).strip() for val in row.values]
        if '大类' in row_values and '施工项目' in row_values:
            header_idx = i
            break

    if header_idx == -1:
        print("❌ 解析终止：未定位到有效表头。")
        return

    df = df_raw.iloc[header_idx + 1:].reset_index(drop=True)
    df.columns = [str(val).strip() for val in df_raw.iloc[header_idx].values]

    # 修复隐式合并单元格
    df['大类'] = df['大类'].ffill()
    df['空间'] = df['空间'].ffill()
    df['施工项目'] = df['施工项目'].replace(r'^\s*$', pd.NA, regex=True)
    df = df.dropna(subset=['施工项目'])

    case_meta = {"customer": "李四", "area": 110, "layout": "三室两厅"}

    payloads = []

    print("[System] 开始执行向量语义与元数据解耦...")
    for index, row in df.iterrows():
        if "结算" in str(row['大类']):
            continue  # 丢弃无用的全局汇总噪音

        # ---------------------------------------------------------
        # 通道 A: 纯语义通道 (用于向量召回，绝不包含具体价格)
        # ---------------------------------------------------------
        page_content = (
            f"施工案例：客户{case_meta['customer']}的{case_meta['area']}平米{case_meta['layout']}项目。 "
            f"工程分类为【{row['大类']}】，具体施工区域在【{row['空间']}】。 "
            f"施工作业项为：{row['施工项目']}。 "
            f"工艺及材料标准：{row['规格/工艺/材料说明']}。 "
            f"计价单位：{row['单位']}。 "
            f"备注信息：{row['备注说明'] if pd.notna(row['备注说明']) else '无'}"
        )

        # ---------------------------------------------------------
        # 通道 B: 刚性元数据通道 (用于精准过滤和 JS 节点聚合计算)
        # ---------------------------------------------------------
        metadata = {
            "customer_name": case_meta['customer'],
            "category": str(row['大类']),
            "space": str(row['空间']),
            "item_name": str(row['施工项目']),
            "unit": str(row['单位']),
            "est_quantity": float(row['预估工程量']) if pd.notna(row['预估工程量']) and str(
                row['预估工程量']).strip() else 0.0,
            "price_labor": float(row['人工单价(元)']) if pd.notna(row['人工单价(元)']) and str(
                row['人工单价(元)']).strip() else 0.0,
            "price_material": float(row['辅材单价(元)']) if pd.notna(row['辅材单价(元)']) and str(
                row['辅材单价(元)']).strip() else 0.0,
            "price_total": float(row['综合单价(元)']) if pd.notna(row['综合单价(元)']) and str(
                row['综合单价(元)']).strip() else 0.0
        }

        payloads.append({
            "page_content": page_content,
            "metadata": metadata
        })

    # 写入 JSONL 文件 (JSON Lines 格式，每行一个独立对象)
    with open(output_jsonl_path, 'w', encoding='utf-8') as f:
        for item in payloads:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    print(f"✅ 解耦完成！已生成 {len(payloads)} 条标准 Payload。")
    print(f"📁 产出文件: {os.path.abspath(output_jsonl_path)}")


if __name__ == "__main__":
    input_file = "mock_budget.csv"
    output_file = "rag_payload.jsonl"
    clean_data_to_rag_payload(input_file, output_file)