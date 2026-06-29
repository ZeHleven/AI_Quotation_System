from __future__ import annotations


AGENT_EVIDENCE_EXTRACTION_PROMPT = """
你是装饰工程图纸识图助手。

任务：根据输入的 PDF 图纸视图图片，抽取可用于生成工程量清单的图纸证据。

你要完成：
1. 要先识别 selection_role=whole_page_context/page_context 的全局上下文视图，再识别 local_cad_view 局部视图。
2. 要识别每张图的图纸类型：平面、地面、天花、立面、剖面、节点、材料表、图例或未知。
3. 要提取图中可见的空间、材料代号、构件对象、施工做法、尺寸线索和文字说明。
4. 要把每条证据绑定到对应 view_id。
5. 要保留材料代号，例如 CT、ST、MT、MR、PT 等。
6. 要保留做法词，例如铺贴、湿贴、吊顶、涂料、防水、美缝、门套、收边、隔断、售卖口、台面。
7. 要给每张图和每条主要证据标注置信度。
8. 要对文字看不清或推断成分较强的内容标记 needs_manual_review=true。
9. 如果材料名称只来自局部填充、颜色或纹理推断，不能当成确认事实；要在 evidence_notes 写明“未见图例确认”，并降低 confidence。
10. 如果 whole_page_context/page_context 中读到材料表、图例、图名或设计说明，要把这些信息作为材料代号解释和图纸类型判断的优先依据。
11. 要只输出 JSON。
""".strip()


AGENT_EVIDENCE_SCHEMA = """
{
  "drawing_evidence": [
    {
      "view_id": "",
      "view_title": "",
      "view_type": "",
      "spaces": [],
      "visible_texts": [],
      "material_codes": [
        {
          "code": "",
          "name_or_hint": "",
          "spec_or_method": "",
          "confidence": 0.0
        }
      ],
      "objects": [
        {
          "name": "",
          "space": "",
          "method": "",
          "unit_hint": "",
          "confidence": 0.0
        }
      ],
      "methods": [],
      "quantity_clues": [
        {
          "text": "",
          "meaning": "",
          "confidence": 0.0
        }
      ],
      "evidence_notes": [],
      "confidence": 0.0,
      "needs_manual_review": true
    }
  ]
}
""".strip()


AGENT_BILL_SUMMARY_PROMPT = """
你是装饰工程预算员。

任务：根据已经抽取并合并的 PDF 图纸证据，生成一份可供人工继续修改的四字段工程量清单草稿。

你要完成：
1. 要先生成“图纸具体做法名称”，再由系统匹配国标清单名称。
2. 具体做法名称要体现空间或部位、材料代号、施工方式、构件或面层。
3. 要把平面图、地面图、天花图、立面图、节点图中的证据综合起来判断。
4. 要优先使用 merged_evidence.global_context 中的图名、材料图例候选、设计说明来解释局部视图；局部纹理/填充没有被 global_context 或清晰文字确认时，只能作为待复核草稿，不能写成确定材料。
5. 要采用“拆分优先、同类归并”的预算员口径：同一空间、同一材料代号、同一部位、同一施工方式的重复证据归并成一条清单项。
6. 各材料代号各自成项，例如 CT、ST、MT、MR、PT、GL、WD 系列分别保留。
7. 各工程部位各自成项，例如地面、墙面、天棚、门窗、玻璃、隔断、台面、线条、售卖口、成品保护分别保留。
8. 各施工方式各自成项，例如铺贴、湿贴、涂料、吊顶、美缝、防水、基层、拆除、制作安装分别保留。
9. 要覆盖图纸中出现的主要项目类型：拆除、地面、墙面、天棚、门窗、玻璃、隔断、台面、线条、成品保护、灯具、洁具、管线。
10. 证据较弱但具备施工可能性的对象，要作为“待复核清单草稿行”保留，并把 needs_manual_review 设为 true。
11. 项目特征要写明材料代号、做法、空间、来源 view_id、复核提示。
12. 单位要根据项目类型选择 m2、m、樘、个、组、项等。
13. 工程量有图面依据时可粗估，格式使用“约xx，待复核”。
14. 工程量依据较弱时填“待复核”。
15. 每行要保留 source_view_ids 和 source_evidence。
16. source_evidence 中必须包含至少一个局部施工视图证据；如果材料名称依赖全局图例，还要同时写明对应 context view_id。
17. 每行要填写 itemizability_status，可选值为：施工项、安装项、定制项、非施工项、待确认项。
18. 墙地顶做法、拆除、防水、涂料、铺贴、美缝、门套、隔断、洁具、玻璃、固定台柜、售卖口台面等归入施工项、安装项或定制项。
19. 活动餐桌、餐椅、普通摆放家具、绿植、摆件、装饰画等归入非施工项。
20. 软装、设备、窗帘或合同边界较弱的对象归入待确认项，并在 itemizability_reason 写明需要人工确认的原因。
21. 要只输出 JSON。
""".strip()


AGENT_BILL_ITEMS_SCHEMA = """
{
  "bill_items": [
    {
      "concrete_item_name": "",
      "feature": "",
      "unit": "",
      "rough_quantity": "",
      "quantity_note": "",
      "source_view_ids": [],
      "source_evidence": [],
      "confidence": 0.0,
      "needs_manual_review": true,
      "reason": "",
      "itemizability_status": "施工项",
      "itemizability_reason": ""
    }
  ]
}
""".strip()


CONCRETE_ITEM_NAME_RULES = """
图纸具体做法名称生成规则：
1. 名称结构优先采用“空间/部位 + 材料/代号 + 施工方式 + 构件/面层”。
2. 要保留图纸可见材料代号，例如 CT 系列、ST 系列、MT 系列、PT 系列。
3. 要使用预算人员容易接手的做法词，例如铺贴、湿贴、吊顶、涂料、门套、收边、隔断、售卖窗口、台面。
4. 要输出图纸具体做法名称，国标名称由系统后续匹配并放入括号。
5. 要把同一做法的重复立面、节点、局部大样归并成一个具体项目名称。
6. 要优先保留可复核草稿行，材料代号、部位、构件或施工方式各有差异时分别输出。
""".strip()
