from __future__ import annotations

import re
from typing import Any, Mapping

from app.models.bidding import BidDraftSection
from app.services.bidding_technical_quality import (
    FORMAL_FIXED_MATERIAL_SECTION_NOS,
    FORMAL_INTENT_DEPTH_MINIMUMS,
    FORMAL_REQUIRED_TOPIC_GROUPS,
    TECHNICAL_REQUIREMENT_FACT_KEYS_BY_INTENT,
    TECHNICAL_FORMAL_DEPTH_INTENTS,
    _missing_formal_execution_loop_groups,
    _missing_formal_topics,
    _normalize_for_contains,
    _paragraph_count,
    _plain_text,
    _requirement_fact_reflected,
    _section_intent,
    _section_no,
)


BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION = "biz4c4_p6_section_template_reinforcement_v3"
BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION = "biz4c4_p7_section_playbook_reinforcement_v2"
BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION = "biz4c4_p9_section_project_fact_reinforcement_v2"

SECTION_TEMPLATE_REINFORCEMENT_HEADING = "专项深化措施"
SECTION_PLAYBOOK_REINFORCEMENT_HEADING = "专业工法与管控清单"
SECTION_EXECUTION_LOOP_HEADING = "实施检查与资料闭环"
SECTION_PROJECT_FACT_REINFORCEMENT_HEADING = "本项目事实应用"

SECTION_TEMPLATE_INTENT_TITLES = {
    "quality_schedule_commitment": "工程质量和工期承诺及保证措施",
    "schedule_plan": "施工总进度计划",
    "construction_organization": "施工组织设计",
    "site_facility_management": "办公室、工具间、材料间管理方案",
    "waste_management_plan": "垃圾清理、堆置、运输、垃圾堆场管理方案",
    "temporary_power_plan": "施工临时用电施工方案",
    "material_procurement_plan": "主要材料采购计划",
    "safety_civil_fire": "安全生产、文明施工、防火施工方案和保证措施",
    "quality_assurance": "施工质量保障措施",
    "material_sample_plan": "主要材料样板提供计划",
    "key_difficulty_analysis": "项目重难点分析",
    "competitive_enhancement": "提升投标竞争力内容",
}

SECTION_TEMPLATE_INTENT_FOCUS = {
    "quality_schedule_commitment": "质量目标、工期节点、资源组织、过程检查和履约闭环",
    "schedule_plan": "总控计划、阶段节点、材料设备进场、资源调配和进度纠偏",
    "construction_organization": "施工部署、组织架构、工序衔接、资源投入和协同管理",
    "site_facility_management": "临时设施布置、办公室管理、工具间管理、材料间管理和台账检查",
    "waste_management_plan": "垃圾分类、临时堆放、场内运输、外运配合和文明施工",
    "temporary_power_plan": "配电系统、线路敷设、照明机具、巡检维护和应急处置",
    "material_procurement_plan": "采购计划、品牌规格、样板报审、进场验收和风险纠偏",
    "safety_civil_fire": "安全责任、教育交底、临时用电、动火消防、文明施工和应急闭环",
    "quality_assurance": "质量目标、样板交底、材料报审、工序验收、实测实量和整改复验",
    "material_sample_plan": "样板清单、规格复核、报审封样、采购联动和资料闭环",
    "key_difficulty_analysis": "重难点识别、针对性对策、过程跟踪、纠偏复验和验收移交",
    "competitive_enhancement": "进度组织、质量样板、安全文明、材料供应、风险响应和服务移交",
}

SECTION_PLAYBOOK_SPECS: dict[str, dict[str, Any]] = {
    "quality_schedule_commitment": {
        "process": ("质量目标确认", "工期节点倒排", "资源组织保障", "检查纠偏履约"),
        "controls": (
            ("质量承诺", "按招标质量目标、施工图纸和验收规范组织样板交底、过程检查和分项验收。", "核对材料报审、样板确认、隐蔽验收、检验批和整改复验结果。", "质量目标分解表、验收记录、整改销项台账"),
            ("工期承诺", "围绕总工期、阶段节点和工作面移交条件倒排资源、材料、劳动力和机具计划。", "检查周计划完成率、关键节点偏差和纠偏措施落实情况。", "总控计划、周计划、节点复盘记录"),
            ("资源保障", "项目经理统筹人员、材料、机具、资金和专业配合资源，保证关键工序连续施工。", "复核资源到位、材料到场、班组交底和机械设备验收状态。", "资源投入表、材料进场台账、设备检查记录"),
            ("履约闭环", "对影响质量和工期的事项建立问题清单、责任分工、完成时限和复查结论。", "复查整改结果、节点恢复计划和发包人/监理确认情况。", "问题整改台账、会议纪要、复查记录"),
        ),
    },
    "schedule_plan": {
        "process": ("施工准备", "深化样板", "基层隐蔽", "面层安装", "整改移交"),
        "controls": (
            ("总控计划", "按施工准备、样板确认、基层隐蔽、面层安装、调试收口和竣工移交设置节点。", "核对总控计划、阶段计划、周计划和日协调执行偏差。", "总进度计划、周计划、节点复盘表"),
            ("材料进场", "基层材料、饰面材料、五金配件、机电末端和成品保护材料按施工段分批报审进场。", "检查品牌规格、样板确认、到货批次、验收结论和堆放状态。", "材料进场计划、报审记录、验收台账"),
            ("设备机具", "测量仪器、临时配电箱、切割打磨机具、移动操作平台和清洁设备按工序提前配置。", "检查机具报验、维护状态、用电保护和作业许可。", "设备进场计划、机具检查记录"),
            ("进度纠偏", "对工作面移交、交叉作业、材料滞后和验收整改引起的偏差及时调整资源和作业顺序。", "复核纠偏措施是否追回节点并消除后续影响。", "进度偏差台账、纠偏措施单、协调纪要"),
        ),
    },
    "construction_organization": {
        "process": ("现场移交", "测量放线", "样板先行", "分区流水", "隐蔽验收", "面层收口", "移交验收"),
        "controls": (
            ("施工部署", "按施工区域、专业工序和工作面移交条件组织分区流水和穿插施工。", "检查施工顺序、交叉界面、材料运输路线和成品保护责任。", "施工部署图、工作面移交单、协调记录"),
            ("组织职责", "项目经理、技术负责人、安全负责人、质量员、材料员、资料员和班组长分工明确。", "核对岗位责任、交底记录、检查频次和问题闭环执行情况。", "组织架构表、岗位职责表、交底记录"),
            ("工序衔接", "测量复核、基层处理、龙骨隐蔽、管线配合、面层施工、机电末端和收口清洁顺序衔接。", "检查隐蔽验收、工序交接、样板标准和实测实量。", "工序交接单、隐蔽验收记录、实测实量记录"),
            ("协同管理", "发包人、监理、总承包单位及其他专业配合事项通过会议纪要和确认单固化责任界面。", "复核协调事项、责任单位、完成时限和复查结论。", "会议纪要、界面确认单、问题销项表"),
        ),
    },
    "site_facility_management": {
        "process": ("布置报审", "分区标识", "领用登记", "消防临电检查", "动态调整"),
        "controls": (
            ("办公室", "用于技术交底、资料整理、会议协调和项目管理，保持整洁、消防有效和用电安全。", "检查资料分类、责任牌、消防器材、插座线路和日常卫生。", "办公室检查表、会议纪要、资料目录"),
            ("工具间", "小型机具、检测工具、劳保用品和周转防护材料分类存放、领用登记、维修保养。", "检查绝缘、防护装置、领退记录、损坏隔离和通道占用。", "工具领用台账、机具保养记录"),
            ("材料间", "材料按类别、规格、批次、使用区域和防潮防火要求分类堆放。", "核对品牌规格、合格证明、样板确认、收发存数量和防护状态。", "材料收发存台账、进场验收单"),
            ("动态管理", "随施工阶段和工作面变化调整容量、通道、消防间距和材料周转路径。", "复查通道、堆放高度、临电安全、消防通道和整改销项。", "现场平面复核记录、整改台账"),
        ),
    },
    "waste_management_plan": {
        "process": ("分类收集", "袋装封闭", "定点堆放", "路线运输", "外运交接", "工完场清"),
        "controls": (
            ("分类清理", "包装物、边角料、拆改废料、粉尘碎屑和可回收材料分类收集，随产随清。", "检查作业面清洁、分类状态、污染控制和成品保护。", "每日清理记录、文明施工检查表"),
            ("堆场管理", "临时堆放点避开消防通道、安全出口、材料区和成品保护区，设置围挡和标识。", "检查堆放范围、高度、防扬散、防火间距和清运时限。", "堆场巡查记录、问题整改单"),
            ("运输外运", "场内运输采用袋装、桶装、覆盖或封闭措施，按指定路线和时间组织。", "检查遗撒、扬尘、通道保护、车辆清洁和消纳交接。", "运输记录、外运交接单、清洁确认单"),
            ("文明闭环", "垃圾清运纳入日检查和周复盘，对混放、超限、堵塞通道和清运滞后及时整改。", "复查整改结果和现场恢复状态。", "垃圾清运台账、整改销项表"),
        ),
    },
    "temporary_power_plan": {
        "process": ("方案报审", "箱体布置", "线路敷设", "机具接入", "巡检维护", "停送电应急"),
        "controls": (
            ("配电系统", "执行三级配电、二级保护、一机一闸一漏一箱，配电箱和开关箱编号标识。", "检查漏电保护器、接地、箱门锁具、防雨防砸和回路标识。", "临电验收记录、配电箱巡检表"),
            ("线路照明", "电缆线路架设或保护敷设，通道、潮湿区域、金属构件附近采取绝缘和防护措施。", "检查绝缘、接头、拖地碾压、照明固定和安全距离。", "线路检查记录、隐患整改单"),
            ("机具用电", "切割、打磨、钻孔、移动平台和照明设备接入前完成机具检查和用电交底。", "核对插头、外壳、开关、防护罩和漏电动作试验。", "机具验收表、班前交底记录"),
            ("巡检应急", "电工每日巡检，停送电执行审批、挂牌、复核和记录，异常时先断电隔离再处置。", "复查故障原因、整改结果、复电确认和人员告知。", "电工巡检表、停送电记录、应急处置记录"),
        ),
    },
    "material_procurement_plan": {
        "process": ("需求计划", "品牌规格复核", "样板报审", "采购加工", "到货验收", "领用追溯"),
        "controls": (
            ("需求计划", "按施工段、工序节点、样板确认和供应周期编制主要材料需求计划。", "检查需求数量、进场批次、加工周期和影响节点。", "材料需求计划、采购计划台账"),
            ("品牌规格", "复核招标品牌、甲指乙供要求、规格型号、颜色纹理、环保性能和图纸适用部位。", "核对样板、合格证明、检测报告和审批意见。", "品牌规格复核表、样板报审单"),
            ("进场验收", "材料到场执行外观检查、数量核对、资料核验、批次标识和分类堆放。", "不合格材料隔离标识并办理退换或替代审批。", "进场验收记录、收发存台账"),
            ("风险纠偏", "对供货滞后、停产、破损、色差和替代材料风险提前预警并组织审批确认。", "复查替代原因、技术参数、样板实物和使用部位。", "风险预警单、替代审批记录"),
        ),
    },
    "safety_civil_fire": {
        "process": ("入场教育", "班前交底", "作业许可", "过程巡查", "隐患整改", "应急复盘"),
        "controls": (
            ("安全教育", "入场人员完成三级教育、专项交底、班前提醒和特殊工种持证核验。", "检查教育签到、交底内容、个人防护和作业许可。", "教育记录、班前交底记录"),
            ("防火动火", "动火作业执行审批、清理、隔离、监护、灭火器配置和作业后复查。", "检查可燃物清理、消防通道、安全出口和灭火器有效性。", "动火审批单、消防巡查记录"),
            ("临边高处", "高处、洞口、临边、移动平台和交叉作业设置防护、警示和监护。", "核对平台稳定、安全带挂设、警戒范围和上下交叉控制。", "高风险作业检查表、整改记录"),
            ("文明施工", "材料定置、粉尘噪声控制、垃圾清运、通道保护和工完场清同步落实。", "复查扬尘、噪声、通道占用、成品污染和整改销项。", "文明施工检查表、隐患销项台账"),
        ),
    },
    "quality_assurance": {
        "process": ("图纸会审", "技术交底", "样板引路", "材料报审", "过程检查", "隐蔽验收", "整改复验"),
        "controls": (
            ("样板引路", "关键分项先做样板，经确认后作为班组交底和大面积施工依据。", "检查样板做法、观感、尺寸偏差、材料一致性和确认手续。", "样板确认记录、交底记录"),
            ("材料报审", "主要材料按品牌、规格、型号、环保性能、合格证明和检测报告报审复核。", "不符合样板、图纸或规范要求的材料不得使用。", "材料报审表、进场验收单"),
            ("工序验收", "基层处理、龙骨隐蔽、管线配合、面层安装和细部收口按工序自检、专检、报验。", "检查隐蔽验收、检验批、实测实量和观感质量。", "隐蔽验收记录、检验批资料、实测实量表"),
            ("质量通病", "针对空鼓开裂、收口粗糙、接缝不顺、色差污染、标高偏差和成品损坏设置预控措施。", "复查整改原因、整改质量、复验结论和资料闭环。", "质量问题台账、整改复验记录"),
        ),
    },
    "material_sample_plan": {
        "process": ("样板清单", "规格复核", "样板制作", "报审封样", "采购联动", "变更替代"),
        "controls": (
            ("样板清单", "按饰面材料、涂料胶粘剂、五金配件、门窗配套、机电末端和收口材料建立清单。", "检查适用部位、规格尺寸、颜色纹理和环保性能。", "样板清单、规格复核表"),
            ("报审封样", "样板制作后编号、拍照、报审、封样留存，确认后作为采购和施工依据。", "核对确认人员、确认时间、适用区域和保存状态。", "样板报审单、封样台账"),
            ("采购联动", "批量采购、到货验收和现场施工均与确认样板比对。", "检查品牌、规格、颜色、纹理、尺寸和检测资料一致性。", "进场比对记录、验收台账"),
            ("替代纠偏", "材料替代必须提交原因、技术参数、样板实物和适用部位，经审批后实施。", "复查替代材料的质量、观感和资料完整性。", "替代审批记录、复核记录"),
        ),
    },
    "key_difficulty_analysis": {
        "process": ("难点识别", "原因分析", "对策制定", "责任分解", "跟踪纠偏", "验收复盘"),
        "controls": (
            ("交叉作业", "识别工作面移交、专业穿插、材料运输、临时用电和成品保护冲突。", "检查界面确认、作业时段、协调纪要和责任闭环。", "重难点清单、界面确认单"),
            ("材料供应", "识别样板确认、定制加工、批次色差、运输破损和替代审批风险。", "复核采购计划、样板封样、到货验收和风险预警。", "材料风险台账、预警单"),
            ("细部质量", "识别基层隐蔽、节点收口、机电末端、阴阳角、接缝和观感质量难点。", "检查样板引路、技术交底、实测实量和整改复验。", "样板记录、实测实量表"),
            ("现场秩序", "识别垃圾清运、动火消防、临电巡检、通道占用和既有设施保护风险。", "复查文明施工、安全隐患和成品保护问题销项。", "安全文明检查表、整改台账"),
        ),
    },
    "competitive_enhancement": {
        "process": ("提前策划", "样板先行", "精细管控", "快速响应", "资料移交", "持续改进"),
        "controls": (
            ("组织协同", "通过总控计划、日协调、周复盘和界面确认提高施工组织效率。", "检查节点达成、协调事项、责任闭环和资源补充。", "协调纪要、节点复盘表"),
            ("质量样板", "以样板先行、实测实量、细部收口和整改复验提升一次成优率。", "复核样板执行、过程检查和观感质量。", "样板记录、质量检查表"),
            ("安全成品", "加强安全文明、临电消防、垃圾清运和成品保护，降低扰动和返工。", "检查隐患整改、保护措施、清洁移交和损坏追责。", "安全文明台账、成品保护记录"),
            ("服务移交", "资料报审、过程验收、整改销项和竣工移交同步推进，快速响应发包人复核意见。", "复查资料完整性、问题关闭率和移交确认。", "资料移交清单、服务响应记录"),
        ),
    },
}


def reinforce_technical_bid_section_template_depth(
    *,
    draft: BidDraftSection,
    component: Mapping[str, Any] | None,
    content: str,
    project_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    section_component = component or {}
    section_no = _section_no(section_component, draft)
    intent = _section_intent(section_component, draft)
    before_text = _plain_text(content)
    before_normalized = _normalize_for_contains(before_text)
    before_paragraph_count = _paragraph_count(content)
    before_visible_length = len(re.sub(r"\s+", "", before_text))
    missing_topics_before = _missing_formal_topics(intent, before_normalized)
    missing_execution_loop_before = _missing_formal_execution_loop_groups(intent, before_normalized)
    minimum = FORMAL_INTENT_DEPTH_MINIMUMS.get(intent)
    min_paragraph_count, min_visible_length = minimum or (0, 0)
    needs_depth = bool(
        minimum
        and (before_paragraph_count < min_paragraph_count or before_visible_length < min_visible_length)
    )
    needs_execution_loop = bool(missing_execution_loop_before)

    if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS or intent not in TECHNICAL_FORMAL_DEPTH_INTENTS or not minimum:
        return _template_reinforcement_result(
            content,
            changed=False,
            reason="fixed_material_section" if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS else "not_scheme_depth_intent",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            missing_topics_before=missing_topics_before,
            missing_topics_after=missing_topics_before,
            missing_execution_loop_before=missing_execution_loop_before,
            missing_execution_loop_after=missing_execution_loop_before,
            added_topics=[],
            added_execution_loop_groups=[],
            added_blocks=[],
        )

    title = _section_title(draft, section_component, intent)
    has_template_block = SECTION_TEMPLATE_REINFORCEMENT_HEADING in str(content or "")
    has_execution_loop_block = SECTION_EXECUTION_LOOP_HEADING in str(content or "")
    if has_template_block and (not needs_execution_loop or has_execution_loop_block):
        return _template_reinforcement_result(
            content,
            changed=False,
            reason="already_reinforced",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            missing_topics_before=missing_topics_before,
            missing_topics_after=missing_topics_before,
            missing_execution_loop_before=missing_execution_loop_before,
            missing_execution_loop_after=missing_execution_loop_before,
            added_topics=[],
            added_execution_loop_groups=[],
            added_blocks=[],
        )
    if has_template_block and needs_execution_loop and not has_execution_loop_block:
        added_lines = [
            f"### {SECTION_EXECUTION_LOOP_HEADING}",
            *_execution_loop_paragraphs(
                title,
                intent,
                project_context,
                groups_to_add=missing_execution_loop_before,
            ),
        ]
        reinforced_content = f"{str(content or '').rstrip()}\n\n" + "\n".join(added_lines).strip() + "\n"
        after_text = _plain_text(reinforced_content)
        after_normalized = _normalize_for_contains(after_text)
        return _template_reinforcement_result(
            reinforced_content,
            changed=True,
            reason="execution_loop_reinforced",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=_paragraph_count(reinforced_content),
            after_visible_length=len(re.sub(r"\s+", "", after_text)),
            missing_topics_before=missing_topics_before,
            missing_topics_after=_missing_formal_topics(intent, after_normalized),
            missing_execution_loop_before=missing_execution_loop_before,
            missing_execution_loop_after=_missing_formal_execution_loop_groups(intent, after_normalized),
            added_topics=[],
            added_execution_loop_groups=missing_execution_loop_before,
            added_blocks=[SECTION_EXECUTION_LOOP_HEADING],
        )

    topic_groups = FORMAL_REQUIRED_TOPIC_GROUPS.get(intent, ())
    topic_labels = [label for label, _keywords in topic_groups]
    topics_to_add = [label for label in topic_labels if label in missing_topics_before]
    added_blocks = [SECTION_TEMPLATE_REINFORCEMENT_HEADING]
    if needs_depth or needs_execution_loop:
        added_blocks.append(SECTION_EXECUTION_LOOP_HEADING)
    added_lines = _template_reinforcement_lines(
        title=title,
        intent=intent,
        topics_to_add=topics_to_add,
        project_context=project_context,
        needs_depth=needs_depth,
        needs_execution_loop=needs_execution_loop,
        execution_loop_groups_to_add=missing_execution_loop_before,
    )
    reinforced_content = f"{str(content or '').rstrip()}\n\n" + "\n".join(added_lines).strip() + "\n"
    after_text = _plain_text(reinforced_content)
    after_normalized = _normalize_for_contains(after_text)
    missing_topics_after = _missing_formal_topics(intent, after_normalized)
    missing_execution_loop_after = _missing_formal_execution_loop_groups(intent, after_normalized)
    return _template_reinforcement_result(
        reinforced_content,
        changed=True,
        reason=(
            "depth_topic_or_execution_loop_reinforced"
            if needs_depth or missing_topics_before or needs_execution_loop
            else "formal_template_alignment_reinforced"
        ),
        section_no=section_no,
        intent=intent,
        before_paragraph_count=before_paragraph_count,
        before_visible_length=before_visible_length,
        after_paragraph_count=_paragraph_count(reinforced_content),
        after_visible_length=len(re.sub(r"\s+", "", after_text)),
        missing_topics_before=missing_topics_before,
        missing_topics_after=missing_topics_after,
        missing_execution_loop_before=missing_execution_loop_before,
        missing_execution_loop_after=missing_execution_loop_after,
        added_topics=topics_to_add,
        added_execution_loop_groups=missing_execution_loop_before if needs_depth or needs_execution_loop else [],
        added_blocks=added_blocks,
    )


def reinforce_technical_bid_section_project_facts(
    *,
    draft: BidDraftSection,
    component: Mapping[str, Any] | None,
    content: str,
    project_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    section_component = component or {}
    section_no = _section_no(section_component, draft)
    intent = _section_intent(section_component, draft)
    before_text = _plain_text(content)
    before_visible_length = len(re.sub(r"\s+", "", before_text))
    if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS or intent not in TECHNICAL_FORMAL_DEPTH_INTENTS:
        return _project_fact_reinforcement_result(
            content,
            changed=False,
            reason="fixed_material_section" if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS else "not_scheme_depth_intent",
            section_no=section_no,
            intent=intent,
            before_visible_length=before_visible_length,
            after_visible_length=before_visible_length,
            fact_items=[],
            skipped_fact_types=[],
        )
    if SECTION_PROJECT_FACT_REINFORCEMENT_HEADING in str(content or ""):
        return _project_fact_reinforcement_result(
            content,
            changed=False,
            reason="already_reinforced",
            section_no=section_no,
            intent=intent,
            before_visible_length=before_visible_length,
            after_visible_length=before_visible_length,
            fact_items=[],
            skipped_fact_types=[],
        )

    title = _section_title(draft, section_component, intent)
    fact_items, skipped_fact_types = _project_fact_reinforcement_items(
        title=title,
        intent=intent,
        content=content,
        project_context=project_context,
    )
    if not fact_items:
        return _project_fact_reinforcement_result(
            content,
            changed=False,
            reason="no_project_fact_gap",
            section_no=section_no,
            intent=intent,
            before_visible_length=before_visible_length,
            after_visible_length=before_visible_length,
            fact_items=[],
            skipped_fact_types=skipped_fact_types,
        )

    lines = _project_fact_reinforcement_lines(title=title, fact_items=fact_items)
    reinforced_content = f"{str(content or '').rstrip()}\n\n" + "\n".join(lines).strip() + "\n"
    after_text = _plain_text(reinforced_content)
    return _project_fact_reinforcement_result(
        reinforced_content,
        changed=True,
        reason="project_fact_reinforced",
        section_no=section_no,
        intent=intent,
        before_visible_length=before_visible_length,
        after_visible_length=len(re.sub(r"\s+", "", after_text)),
        fact_items=fact_items,
        skipped_fact_types=skipped_fact_types,
    )


def reinforce_technical_bid_section_discipline_playbook(
    *,
    draft: BidDraftSection,
    component: Mapping[str, Any] | None,
    content: str,
    project_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    section_component = component or {}
    section_no = _section_no(section_component, draft)
    intent = _section_intent(section_component, draft)
    before_text = _plain_text(content)
    before_paragraph_count = _paragraph_count(content)
    before_visible_length = len(re.sub(r"\s+", "", before_text))
    spec = SECTION_PLAYBOOK_SPECS.get(intent)
    if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS or not spec:
        return _playbook_reinforcement_result(
            content,
            changed=False,
            reason="fixed_material_section" if section_no in FORMAL_FIXED_MATERIAL_SECTION_NOS else "no_playbook_spec",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            added_table_count=0,
            control_item_count=0,
            process_node_count=0,
            added_blocks=[],
        )
    if SECTION_PLAYBOOK_REINFORCEMENT_HEADING in str(content or ""):
        return _playbook_reinforcement_result(
            content,
            changed=False,
            reason="already_reinforced",
            section_no=section_no,
            intent=intent,
            before_paragraph_count=before_paragraph_count,
            before_visible_length=before_visible_length,
            after_paragraph_count=before_paragraph_count,
            after_visible_length=before_visible_length,
            added_table_count=0,
            control_item_count=0,
            process_node_count=0,
            added_blocks=[],
        )
    title = _section_title(draft, section_component, intent)
    lines = _playbook_lines(title=title, intent=intent, spec=spec, project_context=project_context)
    reinforced_content = f"{str(content or '').rstrip()}\n\n" + "\n".join(lines).strip() + "\n"
    after_text = _plain_text(reinforced_content)
    control_count = len(spec.get("controls") or ())
    process_count = len(spec.get("process") or ())
    return _playbook_reinforcement_result(
        reinforced_content,
        changed=True,
        reason="discipline_playbook_reinforced",
        section_no=section_no,
        intent=intent,
        before_paragraph_count=before_paragraph_count,
        before_visible_length=before_visible_length,
        after_paragraph_count=_paragraph_count(reinforced_content),
        after_visible_length=len(re.sub(r"\s+", "", after_text)),
        added_table_count=2,
        control_item_count=control_count,
        process_node_count=process_count,
        added_blocks=[SECTION_PLAYBOOK_REINFORCEMENT_HEADING, "流程节点表", "控制清单表"],
    )


def _project_fact_reinforcement_items(
    *,
    title: str,
    intent: str,
    content: str,
    project_context: Mapping[str, Any] | None,
) -> tuple[list[dict[str, str]], list[str]]:
    normalized_text = _normalize_for_contains(_plain_text(content))
    items: list[dict[str, str]] = []
    skipped: list[str] = []
    context = project_context if isinstance(project_context, Mapping) else {}

    if intent in {
        "construction_organization",
        "schedule_plan",
        "site_facility_management",
        "waste_management_plan",
        "temporary_power_plan",
        "material_procurement_plan",
        "safety_civil_fire",
        "quality_assurance",
        "material_sample_plan",
        "key_difficulty_analysis",
        "competitive_enhancement",
    }:
        zone_names = _project_fact_work_zone_names(context)
        if zone_names:
            covered = any(_normalize_for_contains(name) in normalized_text for name in zone_names[:4])
            if not covered:
                zone_text = "、".join(zone_names[:4])
                items.append(
                    {
                        "fact_type": "work_zone",
                        "label": "施工区域",
                        "sentence": (
                            f"‘{title}’施工区域应用方面，本章措施结合{zone_text}组织实施，工作面移交、材料运输、临时堆放、"
                            "成品保护和交叉作业均按上述区域分区落实。"
                        ),
                    }
                )
        else:
            skipped.append("work_zone")

    if intent in {"quality_schedule_commitment", "schedule_plan"}:
        schedule = context.get("schedule") if isinstance(context.get("schedule"), Mapping) else {}
        schedule_sentence = _project_fact_schedule_sentence(schedule)
        if schedule_sentence:
            expected_durations = _project_fact_schedule_durations(schedule)
            covered = bool(expected_durations and expected_durations <= _project_fact_observed_durations(content))
            if not covered and _normalize_for_contains(schedule_sentence[:40]) not in normalized_text:
                items.append(
                    {
                        "fact_type": "schedule",
                        "label": "工期事实",
                        "sentence": f"‘{title}’工期事实应用方面，{schedule_sentence}，本章进度节点、资源投入和纠偏措施均按该工期事实倒排。",
                    }
                )
        else:
            skipped.append("schedule")

    if intent in {"quality_schedule_commitment", "quality_assurance"}:
        quality = context.get("quality") if isinstance(context.get("quality"), Mapping) else {}
        goal = str(quality.get("goal") or "").strip()
        if goal:
            if _normalize_for_contains(goal) not in normalized_text:
                items.append(
                    {
                        "fact_type": "quality",
                        "label": "质量目标",
                        "sentence": f"‘{title}’质量目标应用方面，招标文件质量目标为“{goal}”，本章材料报审、样板确认、过程检查、验收复核和整改闭环均按该目标组织。",
                    }
                )
        else:
            skipped.append("quality")

    if intent in {"construction_organization", "key_difficulty_analysis", "competitive_enhancement"}:
        scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
        scope_text = _clip(scope.get("scope_text"), 160)
        if scope_text:
            scope_fragment = _normalize_for_contains(scope_text[:32])
            if scope_fragment and scope_fragment not in normalized_text:
                items.append(
                    {
                        "fact_type": "scope",
                        "label": "招标范围",
                        "sentence": f"‘{title}’招标范围应用方面，已抽取到本项目招标范围为“{scope_text}”，本章施工部署、重难点识别和资源组织均围绕该范围展开。",
                    }
                )
        else:
            skipped.append("scope")

    technical_requirements = context.get("technical_requirements") if isinstance(context.get("technical_requirements"), Mapping) else {}
    for fact_key in TECHNICAL_REQUIREMENT_FACT_KEYS_BY_INTENT.get(intent, ()):
        fact = technical_requirements.get(fact_key) if isinstance(technical_requirements, Mapping) else None
        if not isinstance(fact, Mapping) or not str(fact.get("summary") or "").strip():
            skipped.append(f"technical_requirement:{fact_key}")
            continue
        if _requirement_fact_reflected(normalized_text, fact):
            continue
        label = str(fact.get("label") or fact_key).strip()
        summary = _clip(str(fact.get("summary") or "").strip("。；; "), 180)
        items.append(
            {
                "fact_type": f"technical_requirement:{fact_key}",
                "label": label,
                "sentence": f"‘{title}’{label}项目事实应用方面，招标文件提出“{summary}”，本章将其纳入责任分工、检查验收、整改纠偏和资料闭环。",
            }
        )

    return items[:8], _unique_text(skipped)[:12]


def _project_fact_reinforcement_lines(*, title: str, fact_items: list[dict[str, str]]) -> list[str]:
    lines = [
        f"## {SECTION_PROJECT_FACT_REINFORCEMENT_HEADING}",
        f"为避免“{title}”停留在通用表述，本节将已抽取的当前项目事实落实到本章施工组织、过程检查和资料闭环中。",
    ]
    for index, item in enumerate(fact_items, start=1):
        lines.append(f"（{index}）{item['sentence']}")
    return lines


def _project_fact_work_zone_names(project_context: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    raw = project_context.get("work_zone_names")
    if isinstance(raw, list):
        names.extend(str(item).strip() for item in raw if str(item).strip())
    scope = project_context.get("scope")
    if isinstance(scope, Mapping):
        raw_zones = scope.get("work_zones")
        if isinstance(raw_zones, list):
            names.extend(str(item).strip() for item in raw_zones if str(item).strip())
    schedule = project_context.get("schedule")
    if isinstance(schedule, Mapping):
        for zone in schedule.get("zones") or []:
            if isinstance(zone, Mapping) and str(zone.get("name") or "").strip():
                names.append(str(zone.get("name")).strip())
    return _unique_text([name for name in names if name])[:6]


def _project_fact_schedule_sentence(schedule: Mapping[str, Any]) -> str:
    sentence = str(schedule.get("sentence") or "").strip()
    if sentence:
        return sentence
    durations = _project_fact_schedule_durations(schedule)
    if durations:
        return "、".join(f"{days}天" for days in sorted(durations))
    return ""


def _project_fact_schedule_durations(schedule: Mapping[str, Any]) -> set[int]:
    durations: set[int] = set()
    for key in ("total_duration_days", "total_days", "duration_days", "contract_duration_days"):
        value = schedule.get(key)
        if isinstance(value, int) and value > 0:
            durations.add(value)
    for zone in schedule.get("zones") or []:
        if not isinstance(zone, Mapping):
            continue
        value = zone.get("duration_days")
        if isinstance(value, int) and value > 0:
            durations.add(value)
    return durations


def _project_fact_observed_durations(content: str) -> set[int]:
    durations: set[int] = set()
    for match in re.finditer(r"(?:总工期|合同工期|计划工期|工期)[^0-9一二三四五六七八九十百]{0,12}(\d{1,4})\s*(?:日历天|天)", str(content or "")):
        value = int(match.group(1))
        if value > 0:
            durations.add(value)
    return durations


def _template_reinforcement_lines(
    *,
    title: str,
    intent: str,
    topics_to_add: list[str],
    project_context: Mapping[str, Any] | None,
    needs_depth: bool,
    needs_execution_loop: bool,
    execution_loop_groups_to_add: list[str],
) -> list[str]:
    focus = SECTION_TEMPLATE_INTENT_FOCUS.get(intent) or "组织安排、实施流程、检查验收和资料闭环"
    work_zone_phrase = _project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    lines = [
        f"## {SECTION_TEMPLATE_REINFORCEMENT_HEADING}",
        (
            f"围绕“{title}”，本深化内容以{focus}为主线，结合{work_zone_phrase}的施工组织条件，"
            "把章节承诺落实为可执行、可检查、可追溯的现场管理措施。"
        ),
    ]
    topic_map = {label: keywords for label, keywords in FORMAL_REQUIRED_TOPIC_GROUPS.get(intent, ())}
    for label in topics_to_add:
        keywords = topic_map.get(label, ())
        lines.extend(["", f"### {label}", _topic_paragraph(title, label, keywords, project_context)])
    if needs_execution_loop:
        lines.extend(["", f"### {SECTION_EXECUTION_LOOP_HEADING}"])
        lines.extend(
            _execution_loop_paragraphs(
                title,
                intent,
                project_context,
                groups_to_add=execution_loop_groups_to_add,
            )
        )
    return lines


def _playbook_lines(
    *,
    title: str,
    intent: str,
    spec: Mapping[str, Any],
    project_context: Mapping[str, Any] | None,
) -> list[str]:
    work_zone_phrase = _project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    affected_zone_phrase = _project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    process_nodes = [str(item).strip() for item in spec.get("process") or () if str(item).strip()]
    controls = [tuple(str(cell).strip() for cell in row) for row in spec.get("controls") or ()]
    focus = SECTION_TEMPLATE_INTENT_FOCUS.get(intent) or "施工组织、过程检查和资料闭环"
    lines = [
        f"## {SECTION_PLAYBOOK_REINFORCEMENT_HEADING}",
        (
            f"为使“{title}”具备正式技术标的可执行颗粒度，本节将{focus}拆分为流程节点、控制对象、"
            f"工法做法、检查验收和资料记录。‘{title}’各项工作结合{work_zone_phrase}组织实施，涉及{affected_zone_phrase}秩序、"
            "通道、消防、成品保护或交叉作业的事项同步纳入现场协调。"
        ),
        "",
        "### 流程节点表",
        "| 序号 | 流程节点 | 执行要点 | 输出记录 |",
        "| --- | --- | --- | --- |",
    ]
    for index, node in enumerate(process_nodes, start=1):
        lines.append(f"| {index} | {node} | {_process_node_action(node, title)} | {_process_node_record(node)} |")
    lines.extend(
        [
            "",
            "### 控制清单表",
            "| 控制对象 | 工法/做法 | 检查验收 | 资料记录 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in controls:
        padded = list(row[:4]) + [""] * max(0, 4 - len(row))
        lines.append("| " + " | ".join(_table_cell(cell) for cell in padded[:4]) + " |")
    lines.extend(
        [
            "",
            "### 节点复盘与责任闭环",
            (
                f"项目部每周对“{title}”的流程节点和控制清单进行复盘，重点核对责任岗位、计划节点、"
                f"现场实施、检查验收、问题整改和资料归档是否同步完成。‘{title}’未完成事项进入问题销项表，明确责任人、"
                f"完成时限和复查标准，复查合格后关闭；影响发包人、监理、总承包单位或其他专业配合的‘{title}’事项，"
                "通过会议纪要、确认单和影像资料固化处理结果。"
            ),
        ]
    )
    return lines


def _process_node_action(node: str, title: str) -> str:
    actions = {
        "施工准备": "完成进场条件复核、技术交底、资源计划和作业面确认。",
        "深化样板": "完成节点复核、样板制作、样板报审和确认交底。",
        "基层隐蔽": "完成基层处理、隐蔽工程、管线配合和验收确认。",
        "面层安装": "按样板标准组织面层施工、细部收口和机电末端配合。",
        "整改移交": "集中完成整改复验、清洁保护、资料整理和移交确认。",
        "方案报审": "按现场条件复核专项方案并完成报审交底。",
        "箱体布置": "按审批方案布置配电箱、开关箱和标识防护。",
        "线路敷设": "按线路路径、绝缘保护和通道防护要求敷设检查。",
        "机具接入": "机具接入前完成验收、漏电保护试验和班前交底。",
        "巡检维护": "电工按日巡检并记录箱体、线路和保护装置状态。",
        "停送电应急": "执行审批、挂牌、断电隔离、复查和复电确认。",
    }
    return actions.get(node, f"围绕“{title}”完成条件复核、责任交底、过程实施和检查确认。")


def _process_node_record(node: str) -> str:
    if any(term in node for term in ("验收", "移交", "复验")):
        return "验收记录、移交清单、整改销项"
    if any(term in node for term in ("报审", "样板", "封样")):
        return "报审表、样板确认、封样台账"
    if any(term in node for term in ("巡检", "检查", "维护")):
        return "巡检记录、隐患整改单、复查记录"
    if any(term in node for term in ("计划", "准备", "策划")):
        return "计划表、交底记录、条件复核表"
    return "过程记录、检查表、影像资料"


def _topic_paragraph(
    title: str,
    label: str,
    keywords: tuple[str, ...],
    project_context: Mapping[str, Any] | None,
) -> str:
    keyword_text = "、".join(str(item) for item in keywords if str(item).strip()) or label
    work_zone_phrase = _project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域")
    return (
        f"{label}方面，将{keyword_text}纳入“{title}”的控制重点。项目部在进场前完成条件复核、责任交底和计划分解，"
        f"“{title}”的{label}实施过程中结合{work_zone_phrase}分区落实实施记录、过程检查和专业协调；“{title}”的{label}完成后组织验收复核、问题整改、"
        "资料归档和移交确认，确保该专项内容不只停留在原则性描述，而是形成现场可执行的闭环管理。"
    )


def _execution_loop_paragraphs(
    title: str,
    intent: str,
    project_context: Mapping[str, Any] | None,
    *,
    groups_to_add: list[str] | None = None,
) -> list[str]:
    work_zone_phrase = _project_context_phrase(project_context, "work_zone_phrase", "本项目各施工区域及相关配合区域")
    affected_zone_phrase = _project_context_phrase(project_context, "affected_zone_phrase", "各施工区域及周边受影响区域")
    focus = SECTION_TEMPLATE_INTENT_FOCUS.get(intent) or "施工组织和专项措施"
    paragraphs = [
        f"责任分工方面，项目经理统筹“{title}”的总体实施，技术负责人负责技术交底、方法复核和资料要求，质量安全管理人员负责过程巡检、隐患排查和整改复查，材料、资料及各班组责任人按施工段落实具体执行事项。",
        f"‘{title}’实施流程方面，项目部按计划编制、条件复核、样板或交底确认、分区实施、过程检查、阶段验收和移交复盘的顺序推进。‘{title}’涉及{work_zone_phrase}内多专业穿插时，先明确工作面移交、作业时段、材料运输、临时用电和成品保护责任，再组织现场施工。",
        f"‘{title}’检查验收方面，将{focus}转化为日检查、周复盘和节点验收清单，对影响质量、安全、工期、文明施工或移交效果的事项及时记录、会商和确认。‘{title}’验收前核对施工做法、材料状态、成品保护、现场清理和资料完整性，避免问题集中到竣工阶段。",
        f"‘{title}’整改纠偏方面，对检查发现的问题形成问题描述、责任岗位、整改措施、完成时限和复查结论。影响‘{title}’关键节点或交叉作业的事项及时提交发包人、监理及总承包单位协调确认，必要时调整资源投入、作业顺序和验收安排，保证章节措施与现场进度同步落地。",
        f"‘{title}’资料闭环方面，同步归集技术交底、材料报审、样板确认、巡检记录、隐蔽或过程验收、整改复查、会议纪要和影像资料。资料员按‘{title}’建立台账，定期核对现场实施记录与投标承诺的一致性，为过程复核、竣工移交和后续追溯提供依据。",
        f"现场协同方面，对可能影响{affected_zone_phrase}秩序、通道、安全、消防、噪声、扬尘或成品保护的作业提前告知并落实隔离、警示和清洁措施。各班组每日收工前完成工完场清、工具归位、材料覆盖、断电检查和问题反馈，保持施工组织连续受控。",
    ]
    if groups_to_add is None:
        return paragraphs
    paragraph_by_group = dict(
        zip(
            ("责任主体", "实施流程", "检查验收", "整改纠偏", "资料闭环"),
            paragraphs[:5],
        )
    )
    requested = set(groups_to_add)
    return [paragraph for label, paragraph in paragraph_by_group.items() if label in requested]


def _project_fact_sentences(project_context: Mapping[str, Any] | None) -> list[str]:
    sentences: list[str] = []
    schedule = project_context.get("schedule") if isinstance(project_context, Mapping) else None
    if isinstance(schedule, Mapping):
        schedule_sentence = str(schedule.get("sentence") or "").strip()
        duration = schedule.get("total_duration_days")
        if schedule_sentence:
            sentences.append(f"工期组织方面，{schedule_sentence}，相关节点按该工期事实进行倒排和检查。")
        elif isinstance(duration, int) and duration > 0:
            sentences.append(f"工期组织方面，招标文件明确总工期为{duration}天，相关节点按该工期事实进行倒排和检查。")
    quality = project_context.get("quality") if isinstance(project_context, Mapping) else None
    if isinstance(quality, Mapping):
        goal = str(quality.get("goal") or "").strip()
        if goal:
            sentences.append(f"质量控制方面，招标文件质量目标为“{goal}”，本章节措施按该目标组织过程控制、验收复核和整改闭环。")
    scope = project_context.get("scope") if isinstance(project_context, Mapping) else None
    if isinstance(scope, Mapping):
        scope_text = _clip(scope.get("scope_text"), 140)
        if scope_text:
            sentences.append(f"工程范围方面，已抽取到招标范围：{scope_text}，本章节围绕该范围落实现场组织和过程控制。")
    return sentences[:3]


def _project_context_phrase(project_context: Mapping[str, Any] | None, key: str, fallback: str) -> str:
    if isinstance(project_context, Mapping):
        value = str(project_context.get(key) or "").strip()
        if value:
            return value
    return fallback


def _section_title(draft: BidDraftSection, component: Mapping[str, Any], intent: str) -> str:
    for value in (
        draft.section_title,
        component.get("component_title") if isinstance(component, Mapping) else None,
        SECTION_TEMPLATE_INTENT_TITLES.get(intent),
    ):
        text = str(value or "").strip()
        if text:
            text = re.sub(r"^\s*7[._]3[._]\d+\s*", "", text, flags=re.I).strip()
            return text[:160]
    return "本章节"


def _template_reinforcement_result(
    content: str,
    *,
    changed: bool,
    reason: str,
    section_no: str,
    intent: str,
    before_paragraph_count: int,
    before_visible_length: int,
    after_paragraph_count: int,
    after_visible_length: int,
    missing_topics_before: list[str],
    missing_topics_after: list[str],
    missing_execution_loop_before: list[str],
    missing_execution_loop_after: list[str],
    added_topics: list[str],
    added_execution_loop_groups: list[str],
    added_blocks: list[str],
) -> dict[str, Any]:
    return {
        "version": BID_TECHNICAL_SECTION_TEMPLATE_REINFORCEMENT_VERSION,
        "status": "applied" if changed else "no_action",
        "changed": changed,
        "reason": reason,
        "content": content,
        "section_no": section_no,
        "intent": intent,
        "paragraph_count_before": before_paragraph_count,
        "paragraph_count_after": after_paragraph_count,
        "visible_length_before": before_visible_length,
        "visible_length_after": after_visible_length,
        "missing_topics_before": list(missing_topics_before)[:20],
        "missing_topics_after": list(missing_topics_after)[:20],
        "missing_execution_loop_before": list(missing_execution_loop_before)[:20],
        "missing_execution_loop_after": list(missing_execution_loop_after)[:20],
        "added_topic_count": len(added_topics),
        "added_topics": list(added_topics)[:20],
        "added_execution_loop_group_count": len(added_execution_loop_groups),
        "added_execution_loop_groups": list(added_execution_loop_groups)[:20],
        "added_blocks": list(added_blocks)[:12],
    }


def _project_fact_reinforcement_result(
    content: str,
    *,
    changed: bool,
    reason: str,
    section_no: str,
    intent: str,
    before_visible_length: int,
    after_visible_length: int,
    fact_items: list[dict[str, str]],
    skipped_fact_types: list[str],
) -> dict[str, Any]:
    fact_types = [str(item.get("fact_type") or "") for item in fact_items if str(item.get("fact_type") or "")]
    labels = [str(item.get("label") or "") for item in fact_items if str(item.get("label") or "")]
    return {
        "version": BID_TECHNICAL_SECTION_PROJECT_FACT_REINFORCEMENT_VERSION,
        "status": "applied" if changed else "no_action",
        "changed": changed,
        "reason": reason,
        "content": content,
        "section_no": section_no,
        "intent": intent,
        "visible_length_before": before_visible_length,
        "visible_length_after": after_visible_length,
        "fact_count": len(fact_items),
        "fact_types": fact_types[:20],
        "fact_labels": labels[:20],
        "skipped_fact_types": list(skipped_fact_types)[:20],
        "added_blocks": [SECTION_PROJECT_FACT_REINFORCEMENT_HEADING] if changed else [],
    }


def _playbook_reinforcement_result(
    content: str,
    *,
    changed: bool,
    reason: str,
    section_no: str,
    intent: str,
    before_paragraph_count: int,
    before_visible_length: int,
    after_paragraph_count: int,
    after_visible_length: int,
    added_table_count: int,
    control_item_count: int,
    process_node_count: int,
    added_blocks: list[str],
) -> dict[str, Any]:
    return {
        "version": BID_TECHNICAL_SECTION_PLAYBOOK_REINFORCEMENT_VERSION,
        "status": "applied" if changed else "no_action",
        "changed": changed,
        "reason": reason,
        "content": content,
        "section_no": section_no,
        "intent": intent,
        "paragraph_count_before": before_paragraph_count,
        "paragraph_count_after": after_paragraph_count,
        "visible_length_before": before_visible_length,
        "visible_length_after": after_visible_length,
        "added_table_count": added_table_count,
        "control_item_count": control_item_count,
        "process_node_count": process_node_count,
        "added_blocks": list(added_blocks)[:12],
    }


def _table_cell(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace("|", "｜")
    return text or "-"


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
