from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from app.core.time_utils import app_local_naive, parse_iso_datetime
from app.models.budget_project import BudgetProjectProfile
from app.models.project_progress import Project, ProjectStage, ProjectTask, ProjectTaskEvent, ProjectTaskEvidence
from app.models.user import User
from app.services.rbac import has_any_role


PROJECT_STATUSES = {"planning", "active", "paused", "completed", "cancelled"}
PROJECT_RISK_LEVELS = {"normal", "warning", "delayed", "blocked"}
STAGE_STATUSES = {"todo", "started", "progressing", "submitted", "done"}
TASK_STATUSES = {"todo", "started", "progressing", "submitted", "done", "blocked", "cancelled"}
TASK_TERMINAL_STATUSES = {"done", "cancelled"}
TASK_PROGRESS_BY_STATUS = {
    "todo": 0,
    "started": 25,
    "progressing": 50,
    "submitted": 80,
    "done": 100,
}
TASK_STATUS_LABELS = {
    "todo": "未开始",
    "started": "已开始",
    "progressing": "进行中",
    "submitted": "已提交",
    "done": "已完成",
    "blocked": "已阻塞",
    "cancelled": "已取消",
}
TASK_ROLLBACK_TARGETS = {
    "started": {"todo"},
    "progressing": {"started", "todo"},
    "submitted": {"progressing", "started", "todo"},
    "done": {"submitted", "progressing", "started", "todo"},
}
TASK_TRANSITIONS = {
    ("todo", "started"),
    ("todo", "progressing"),
    ("todo", "submitted"),
    ("todo", "done"),
    ("todo", "blocked"),
    ("todo", "cancelled"),
    ("started", "progressing"),
    ("started", "submitted"),
    ("started", "done"),
    ("started", "blocked"),
    ("started", "cancelled"),
    ("progressing", "submitted"),
    ("progressing", "done"),
    ("progressing", "blocked"),
    ("progressing", "cancelled"),
    ("submitted", "progressing"),
    ("submitted", "done"),
    ("submitted", "blocked"),
    ("submitted", "cancelled"),
    ("blocked", "todo"),
    ("blocked", "started"),
    ("blocked", "progressing"),
    ("blocked", "submitted"),
    ("blocked", "done"),
    ("blocked", "cancelled"),
}
TASK_PRIORITIES = {"low", "normal", "high", "urgent"}
TASK_SOURCES = {"manual", "system"}
TASK_EVIDENCE_TYPES = {"file", "link", "text"}
TASK_EVIDENCE_STATUSES = {"active", "removed"}
TASK_EVIDENCE_POLICIES = {"none", "soft_reminder", "complete_required"}

DEFAULT_PROJECT_STAGES = [
    {"key": "initiation", "name": "立项", "weight": 5, "owner_role": "商务/市场"},
    {"key": "quote", "name": "报价", "weight": 15, "owner_role": "预算/成本"},
    {"key": "contract", "name": "合同", "weight": 10, "owner_role": "商务/财务"},
    {"key": "design", "name": "设计深化", "weight": 15, "owner_role": "设计"},
    {"key": "material_confirmation", "name": "材料确认", "weight": 10, "owner_role": "成本/采购/项目经理"},
    {"key": "procurement", "name": "采购", "weight": 10, "owner_role": "采购"},
    {"key": "construction", "name": "施工", "weight": 25, "owner_role": "项目经理/施工"},
    {"key": "acceptance_settlement", "name": "验收结算", "weight": 10, "owner_role": "项目经理/财务"},
]

SINGLE_USER_TRIAL_TEMPLATE_KEY = "single_user_fitout_v1"
SINGLE_USER_TRIAL_TEMPLATE_NAME = "单人试运行工程模板"
SINGLE_USER_TRIAL_TEMPLATE_DAYS = 30
SINGLE_USER_TRIAL_TASKS = [
    {
        "stage_key": "initiation",
        "title": "确认项目基本信息",
        "priority": "high",
        "due_day": 1,
        "next_action": "补齐客户、地址、范围、联系人和关键时间",
    },
    {
        "stage_key": "initiation",
        "title": "整理现场照片和原始需求",
        "priority": "normal",
        "due_day": 2,
        "next_action": "收集现场条件、客户口述需求和限制条件",
    },
    {
        "stage_key": "quote",
        "title": "完成需求清单标准化",
        "priority": "high",
        "due_day": 4,
        "next_action": "把客户需求整理成可报价清单",
    },
    {
        "stage_key": "quote",
        "title": "完成 AI 报价预审",
        "priority": "high",
        "due_day": 5,
        "next_action": "核对工程量、底价参考和疑似漏项",
    },
    {
        "stage_key": "quote",
        "title": "确认报价版本并发给客户",
        "priority": "high",
        "due_day": 6,
        "next_action": "记录报价版本、金额和客户反馈",
    },
    {
        "stage_key": "contract",
        "title": "确认合同范围和付款节点",
        "priority": "high",
        "due_day": 8,
        "next_action": "核对施工范围、工期、付款和变更条款",
    },
    {
        "stage_key": "contract",
        "title": "完成合同签订或待签状态记录",
        "priority": "normal",
        "due_day": 9,
        "next_action": "记录合同签订状态和未决事项",
    },
    {
        "stage_key": "design",
        "title": "确认平面和关键节点做法",
        "priority": "high",
        "due_day": 12,
        "next_action": "确认影响报价和施工的关键节点",
    },
    {
        "stage_key": "design",
        "title": "确认设计变更清单",
        "priority": "normal",
        "due_day": 14,
        "next_action": "记录新增、取消和调整项目",
    },
    {
        "stage_key": "material_confirmation",
        "title": "确认主材品牌和规格",
        "priority": "high",
        "due_day": 16,
        "next_action": "确认客户指定材料和替代材料",
    },
    {
        "stage_key": "material_confirmation",
        "title": "确认材料样板或图片",
        "priority": "normal",
        "due_day": 17,
        "next_action": "保留客户确认凭证",
    },
    {
        "stage_key": "procurement",
        "title": "确认采购清单和到货周期",
        "priority": "high",
        "due_day": 19,
        "next_action": "核对长周期材料和到货风险",
    },
    {
        "stage_key": "procurement",
        "title": "确认供应商下单状态",
        "priority": "normal",
        "due_day": 21,
        "next_action": "记录已下单、待付款、待到货事项",
    },
    {
        "stage_key": "construction",
        "title": "确认开工条件",
        "priority": "high",
        "due_day": 22,
        "next_action": "确认进场、交底、水电和保护条件",
    },
    {
        "stage_key": "construction",
        "title": "记录现场施工进展",
        "priority": "high",
        "due_day": 25,
        "next_action": "每天更新关键完成面、阻塞点和下一步",
    },
    {
        "stage_key": "construction",
        "title": "确认变更和签证事项",
        "priority": "normal",
        "due_day": 27,
        "next_action": "记录需客户确认的增减项",
    },
    {
        "stage_key": "acceptance_settlement",
        "title": "完成现场验收问题记录",
        "priority": "high",
        "due_day": 29,
        "next_action": "记录整改项、责任人和完成时间",
    },
    {
        "stage_key": "acceptance_settlement",
        "title": "完成结算资料整理",
        "priority": "normal",
        "due_day": 30,
        "next_action": "整理合同、报价、变更、签证和验收记录",
    },
]

QISHENG_EPC_FULL_TEMPLATE_KEY = "qisheng_epc_full_v1"
QISHENG_EPC_COMPACT_TEMPLATE_KEY = "qisheng_epc_compact_v1"
QISHENG_EPC_TEMPLATE_NAME = "旗胜 EPC 项目管理流程模板"
QISHENG_EPC_TEMPLATE_DAYS = 180
QISHENG_EPC_STAGE_DEFINITIONS = [
    {"key": "market_development", "name": "市场开发阶段", "weight": 10, "owner_role": "市场部"},
    {"key": "design_solution", "name": "设计方案阶段", "weight": 20, "owner_role": "设计部"},
    {"key": "tender_procurement", "name": "招投标阶段", "weight": 25, "owner_role": "成本部/采购部"},
    {"key": "production_delivery", "name": "生产交付阶段", "weight": 35, "owner_role": "项目部"},
    {"key": "after_sales", "name": "售后服务阶段", "weight": 10, "owner_role": "客服部"},
]

QISHENG_EPC_PROCESS_ROWS = """
market_development|客户获取|自拓开发|客户信息：姓名、电话、微信；项目信息：类型、面积、地址；内部教练：对接人、关键人、决策人|市场部|/|总经办|客户档案登记表|0
market_development|客户获取|资源介绍|客户信息：姓名、电话、微信；项目信息：类型、面积、地址；内部教练：对接人、关键人、决策人|市场部|/|总经办|客户档案登记表|0
market_development|客户获取|平台赋能|客户信息：姓名、电话、微信；项目信息：类型、面积、地址；内部教练：对接人、关键人、决策人|市场部|/|总经办|客户档案登记表|0
market_development|项目攻坚|客户拜访/参观考察 公司模式介绍|客户需求与痛点分析；提供解决方案；案例呈现；参观公司|市场部|/|总经办|客户洽谈记录单；设计需求调查表；原始图纸汇总；项目图像资料|1
market_development|项目攻坚|项目入库|供应商信息资料登记，提资，审核完成入库|市场部|/|总经办|客户洽谈记录单；设计需求调查表；原始图纸汇总；项目图像资料|0
market_development|项目攻坚|项目调研|现场勘察，信息收集、需求了解、客户需求调查表完善；项目原始图纸收集：建筑图|市场部|设计部/工管部|总经办|客户洽谈记录单；设计需求调查表；原始图纸汇总；项目图像资料|1
market_development|项目攻坚|设计咨询与服务|设计理念；平面草图；意向图片：概念方案|市场部|设计部|总经办|主案设计师介绍方案；客户意见反馈表|1
market_development|项目攻坚|商务洽谈|项目投资预算，设计报价，服务流程展示|市场部|成本部/工管部|总经办|商务洽谈报告|1
market_development|设计签约|设计合同签署|重要步骤|市场部|/|总经办|全案合同及附件|1
market_development|设计签约|设计定金收取|定金40%，到账确认|市场部|/|总经办|到账水单|1
design_solution|项目立项|内部立项|确认工作界面负责人；设计任务书：内容及周期|市场部|设计部/工管部|总经办|立项会议及纪要；工作界面划分表|1
design_solution|项目立项|勘验现场|组织甲方、设计及工程负责人项目现场勘查|市场部|设计部/工管部|总经办|勘察信息登记表；现场图像资料|0
design_solution|项目立项|资料收集|专业图纸收集：建筑、结构、外立面、机电、暖通、燃气、消防，智能界面确认|市场部|设计部/工管部|总经办|图纸档案文件汇总|1
design_solution|项目立项|设计进度计划|制定设计工作计划|市场部|设计部/工管部|总经办|项目总体工作计划书|1
design_solution|项目立项|汇报提交|向甲方提交设计工作计划书及服务流程|市场部|设计部/工管部|总经办|会议及纪要；工作确认单|0
design_solution|方案设计|方案设计-方案汇报|完成平面布局方案设计1-3稿；协同主案设计师向甲方汇报|市场部|设计部/工管部|总经办|会议及纪要；甲方意见反馈表；工作确认单|1
design_solution|效果设计|效果设计-方案汇报|完成效果方案设计；协同主案设计师向甲方进行汇报|市场部|设计部/工管部|总经办|会议及纪要；甲方意见反馈表；工作确认单|0
design_solution|施工图设计|施工图设计-方案汇报|完成全套施工图设计；协同主案设计师向甲方进行汇报|市场部|设计部/工管部|总经办|会议及纪要；甲方意见反馈表；工作确认单|1
design_solution|项目预算|成本预算|组织内部各部门对项目成本核算会议：设计、工程、软装|成本部|设计部/工管部|总经办|会议及纪要；成本概算汇总表|1
design_solution|方案深化|专业配合汇总审核|组织各专项配合方：机电、消防、水暖、结构门窗等，督导参与第三方技术会议|设计部|采购部/市场部/工管部|总经办|机电图纸及设备清单；水暖设备图纸及设备清单；改造图纸及材料清单|0
design_solution|方案深化|硬装物料手册|硬装色块、木皮、石材、五金洁具、灯具照明汇总|设计部|采购部|总经办|硬装物料表；材料样板封样|0
design_solution|方案深化|固装物料手册|固装图纸、色板、五金、电器、石材汇总|设计部|采购部|总经办|固装物料表；材料样板封样|0
design_solution|方案深化|软装物料手册|软装方案产品、皮布板、配饰色板汇总|设计部|采购部|总经办|软装物料表；材料样板封样|0
design_solution|方案深化|图纸会审|施工图会审会议：基础施工图及精装施工图|设计部|成本部/采购部/工管部|总经办|会议及会议纪要；提交施工图设计节点|1
design_solution|方案深化|工程项目预算书|成本整理总体施工报价预算汇总成册|成本部|采购部|总经办|施工预算报价书|1
design_solution|方案深化|设计成果交付|设计全套图纸成果向甲方汇报并确认：设计成果、各项主材确认、项目预算报价书、精装/家具/生活居品预算单|设计部|市场部|总经办|移交成果确认表|1
design_solution|商务收款|设计进度款收取|达到设计款95%，竣工后收取5%尾款|市场部|/|总经办|到账水单|1
tender_procurement|项目投标|投标报名|参与投标，资料递交与接收|市场部|/|总经办|投标报名文件|0
tender_procurement|项目投标|招标资料接收|招标文件接收，建群共享每部团队说明|市场部|/|总经办|招标文件|1
tender_procurement|项目投标|现场勘察|技术人员现场勘察，信息收集与项目了解|成本部|工管部|总经办|勘察报告|1
tender_procurement|项目投标|项目提疑|问题汇总，问题递交，并查收回复结果|成本部|工管部|总经办|答疑会汇总表|1
tender_procurement|项目投标|保函/保证金递交|按照招标要求提交保证金及保函|成本部|财务部|总经办|回执单|0
tender_procurement|项目投标|商务/资格/技术标制作|商务标、资格标、技术标的制作确认|成本部|工管部/采购部|总经办|投标文件|1
tender_procurement|项目投标|成本测算+决策|成本测算、投标利润确认，出标前确认|成本部|总经办/采购部|总经办|投标报价表|1
tender_procurement|项目投标|标书装/打/印/封/送|标准文件的确认打印盖章，封标，要求时间递交|市场部|成本部|总经办|投标递交文件|0
tender_procurement|项目投标|述标+现场投标|现场述标+甲方提疑答疑|工管部|市场部/成本部|总经办|述标文件+现场述标|0
tender_procurement|项目投标|回标|二次/三次回标递交定价|市场部|成本部|总经办|回标定价表|0
tender_procurement|项目投标|中标/合约|中标通知书接收+签署合同|市场部|法务部|总经办|中标通知书|1
tender_procurement|项目投标|工程预付款收取|收取工程预付款确认|项目部|市场部|总经办|到账水单|1
tender_procurement|劳务招采|项目招标公示|招标信息公示，发招标资料|采购部|成本部|总经办|招标信息记录|0
tender_procurement|劳务招采|供应商入库|供应商信息资料登记，提资，审核完成入库|采购部|成本部|总经办|入库资料|0
tender_procurement|劳务招采|供应商考察|供应商项目、技术、公示、实力考察|采购部|成本部/工管部|总经办|考察报告|0
tender_procurement|劳务招采|材料/劳务成本清单拆分|劳务清单及材料清单拆分整理，具备招标外发条件|成本部|项目部|总经办|分包招标清单表|1
tender_procurement|劳务招采|项目图纸/工艺/清单交代|项目概况、工艺、要求、答疑、图纸交底|采购部|成本部/工管部|总经办|图纸+清单/交底记录|0
tender_procurement|劳务招采|第一轮评标|价格分70%+综合分30%|采购部|成本部/工管部|总经办|评标记录|0
tender_procurement|劳务招采|多轮评标|价格分70%+综合分30%|采购部|成本部/工管部|总经办|评标记录|0
tender_procurement|劳务招采|中标决策/发中标通知|中标单位决策+发中标通知|采购部|成本部/工管部|总经办|中标通知书|0
tender_procurement|劳务招采|分包合约签署|合同签署闭环|工管部|采购部|总经办|合约签署备案|1
production_delivery|施工前期 隐蔽工程|开工仪式|组织设计方、工程方、甲方做开工仪式|项目部|工管部|总经办|开工资料档案登记表|1
production_delivery|施工前期 隐蔽工程|现场放线+验线|现场放线、复核验线|项目部|工管部|总经办|放线验收表|0
production_delivery|施工前期 隐蔽工程|制定总控计划|进度计划、节点计划、交付计划|项目部|工管部|总经办|计划表|1
production_delivery|施工前期 隐蔽工程|安全文明施工管理|8S 管理、现场规范|项目部|工管部|总经办|日报汇报表|0
production_delivery|施工前期 隐蔽工程|项目安全培训|每日施工安全培训|项目部|工管部|总经办|会议+培训图文反馈|0
production_delivery|施工前期 隐蔽工程|日报/周报|项目每日、每周反馈施工进度，现状反馈|项目部|工管部|总经办|汇报反馈表|0
production_delivery|施工前期 隐蔽工程|基层材料下单|辅材采购、进度跟进|项目部|采购部|总经办|辅材材料下单表|0
production_delivery|施工前期 隐蔽工程|主材确认/主材下单|主材确认、采购、跟进|项目部|采购部|总经办|主材采购下单表|1
production_delivery|施工前期 隐蔽工程|隐蔽工程施工|水电、防水等隐蔽施工|项目部|工管部|总经办|隐蔽工程验收报告及备案；同步共享业主|0
production_delivery|施工前期 隐蔽工程|隐蔽工程验收|三方验收、签字确认|项目部|工管部|总经办|隐蔽工程验收报告及备案|1
production_delivery|施工中期|基层施工|监督施工进度、质量、规范、安全|项目部|工管部|总经办|工程管理节点日报|0
production_delivery|施工中期|基层整改|整改消项、复查闭环|项目部|工管部|总经办|基层整改消项验收表备案|0
production_delivery|施工中期|基层验收|甲方/设计/工程三方验收|项目部|工管部|总经办|基层验收表甲方签字确认|1
production_delivery|施工中期|面层施工/主材安装|天地墙主材/面层施工|项目部|工管部/采购部|总经办|工程管理节点日报|1
production_delivery|施工中期|定制品复尺/出图|现场复尺、出图、数据追踪|项目部|采购部/设计部|总经办|产品深化图|0
production_delivery|施工中期|定制/固装下单生产|定制品、固装采购、进度跟进|项目部|采购部/设计部|总经办|固装产品采购下单|0
production_delivery|施工中期|变更增项/窝工索赔|变更确认，认量认价，窝工索赔文件及对接确认|项目部|成本部|总经办|签证单/索赔手续表|1
production_delivery|商务款|月进度款申请|达到工程预算款80%，向业主发起请款单请款|项目部|成本部|总经办|到账水单|1
production_delivery|施工后期|定制产品进场安装|进场、进度、质量、安全、成品保护|项目部|采购部|总经办|工程管理节点报告备案|0
production_delivery|施工后期|固装进度安装|监督协调固装安装进度、质量、规范、安全、成品保护|项目部|采购部|总经办|内部固装验收签字确认备案|0
production_delivery|施工后期|保洁工作|粗保洁+精保洁|项目部|采购部|总经办|保洁报价审核及照片收集整理|0
production_delivery|施工后期|精装预验收|内部整体验收|项目部|成本部/工管部|总经办|精装预验收报告备案|1
production_delivery|施工后期|精装整改消项|责任整改、消项、复查|项目部|工管部|总经办|精装最终整改验收报告备案并提交整改建议|0
production_delivery|施工后期|竣工精装验收|甲方/设计/工程三方竣工验收，签字确认|项目部|成本部/工管部|总经办|精装验收表甲方签字确认及备案|1
production_delivery|商务款|竣工款收款|收取合同额90%竣工款|项目部|成本部|总经办|到账水单|0
production_delivery|商务款|结算确认|甲方结算、劳务分包结算|项目部|成本部|总经办|结算确认书|1
production_delivery|商务款|结算款收款|结算资料核对，报审，确认；合同总额97%|项目部|成本部|总经办|到账水单|1
after_sales|客户满意度|满意度调查+回访|完成客户评价调查表|客服部|/|总经办|客户满意度调查表备案|1
after_sales|客户维护|客户维护+客情关系|持续客户链接关系，加深信任度|客服部|/|总经办|跟进记录|0
after_sales|服务响应|质保内|收到客诉后24小时内回复，48小时内给予解决方案，3天内完成售后处理方案|客服部|工管部|总经办|售后服务回执表|1
after_sales|服务响应|质保期后|收到客诉后48小时内回复，72小时内给予解决方案，7天内完成售后处理|客服部|工管部|总经办|售后服务回执表|0
after_sales|客户回访|客户回访次数|每月2次联系沟通，了解使用情况，询问新项目商机|客服部|/|总经办|客户回访记录表备案|1
after_sales|客户裂变|挖掘客户新项目/客户转介绍|提供增值服务，生活关怀，加强维护粘度，拜访|客服部|/|总经办|微信记录；电话记录；照片记录|0
after_sales|商务款|工程质保金回款|资料齐全、到账|客服部|/|总经办|到账水单|1
""".strip()


@dataclass(frozen=True)
class EventContext:
    ip_address: str | None = None
    user_agent: str | None = None
    trace_id: str | None = None


def now_local_naive() -> datetime:
    return app_local_naive()


def to_local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return app_local_naive(value)


def parse_local_datetime(value: str | None, *, field_name: str = "datetime") -> datetime | None:
    if value is None:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    try:
        parsed = parse_iso_datetime(raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"INVALID_{field_name.upper()}") from exc
    return to_local_naive(parsed)


def format_dt(value: datetime | None) -> str | None:
    value = to_local_naive(value)
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def clean_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _parse_epc_template_rows() -> list[dict]:
    rows: list[dict] = []
    for index, raw_line in enumerate(QISHENG_EPC_PROCESS_ROWS.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 9:
            raise RuntimeError(f"Invalid EPC template row #{index}: {raw_line}")
        stage_key, content, node, standard, lead, assist, supervise, deliverable, compact = parts
        rows.append(
            {
                "stage_key": stage_key,
                "content": content,
                "node": node,
                "standard": standard,
                "lead_department": lead,
                "assist_department": assist,
                "supervise_department": supervise,
                "deliverable": deliverable,
                "compact": compact == "1",
                "sort_order": index,
            }
        )
    return rows


QISHENG_EPC_TEMPLATE_TASKS = _parse_epc_template_rows()
QISHENG_EPC_A_LEVEL_GATE_NODE_KEYS = {
    ("design_solution", "设计成果交付"),
    ("production_delivery", "隐蔽工程验收"),
    ("production_delivery", "竣工精装验收"),
    ("production_delivery", "结算确认"),
}


def qisheng_epc_template_key(mode: str | None) -> str:
    return QISHENG_EPC_FULL_TEMPLATE_KEY if (mode or "").strip() == "full" else QISHENG_EPC_COMPACT_TEMPLATE_KEY


def qisheng_epc_template_tasks(mode: str | None) -> list[dict]:
    if qisheng_epc_template_key(mode) == QISHENG_EPC_FULL_TEMPLATE_KEY:
        return QISHENG_EPC_TEMPLATE_TASKS
    return [item for item in QISHENG_EPC_TEMPLATE_TASKS if item["compact"]]


def epc_task_description(item: dict) -> str:
    return "\n".join(
        [
            f"流程内容：{item['content']}",
            f"执行标准：{item['standard']}",
            f"辅助部门：{item['assist_department']}",
            f"监督部门：{item['supervise_department']}",
            f"成果文件：{item['deliverable']}",
        ]
    )


def parse_epc_task_meta(description: str | None) -> dict | None:
    if not description:
        return None
    labels = {
        "流程内容": "content",
        "执行标准": "standard",
        "辅助部门": "assist_department",
        "监督部门": "supervise_department",
        "成果文件": "deliverable",
    }
    meta: dict[str, str] = {}
    for raw_line in description.splitlines():
        line = raw_line.strip()
        for label, key in labels.items():
            prefix = f"{label}："
            if line.startswith(prefix):
                meta[key] = line[len(prefix) :].strip()
    return meta or None


def qisheng_epc_template_item(stage_key: str | None, node: str | None) -> dict | None:
    stage_key = (stage_key or "").strip()
    node = (node or "").strip()
    if not stage_key or not node:
        return None
    for item in QISHENG_EPC_TEMPLATE_TASKS:
        if item["stage_key"] == stage_key and item["node"] == node:
            return item
    return None


def is_qisheng_epc_a_level_gate_item(item: dict | None) -> bool:
    if not item:
        return False
    return (item.get("stage_key"), item.get("node")) in QISHENG_EPC_A_LEVEL_GATE_NODE_KEYS


def normalize_evidence_policy(value: str | None) -> str:
    normalized = (value or "none").strip()
    if normalized not in TASK_EVIDENCE_POLICIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TASK_EVIDENCE_POLICY")
    return normalized


def derive_task_evidence_fields(
    *,
    source_id: str | None,
    stage_key: str | None = None,
    title: str | None = None,
    description: str | None = None,
    epc_item: dict | None = None,
    requirement_source: str = "template",
    complete_required_enabled: bool = False,
) -> dict:
    source_id = (source_id or "").strip()
    fields = {
        "evidence_requirement": None,
        "evidence_policy": "none",
        "is_key_node": False,
    }
    if source_id == SINGLE_USER_TRIAL_TEMPLATE_KEY or source_id not in {QISHENG_EPC_COMPACT_TEMPLATE_KEY, QISHENG_EPC_FULL_TEMPLATE_KEY}:
        return fields

    item = epc_item or qisheng_epc_template_item(stage_key, title)
    epc_meta = parse_epc_task_meta(description) if description else None
    template_requirement = clean_text(item.get("deliverable") if item else None, 2000)
    description_requirement = clean_text(epc_meta.get("deliverable") if epc_meta else None, 2000)
    if requirement_source == "description":
        requirement = description_requirement
    else:
        requirement = template_requirement or description_requirement

    if source_id == QISHENG_EPC_COMPACT_TEMPLATE_KEY:
        is_key_node = True
    elif item:
        is_key_node = bool(item.get("compact"))
    else:
        is_key_node = False

    fields["evidence_requirement"] = requirement
    if requirement and complete_required_enabled and is_qisheng_epc_a_level_gate_item(item):
        fields["evidence_policy"] = "complete_required"
    else:
        fields["evidence_policy"] = "soft_reminder" if requirement else "none"
    fields["is_key_node"] = is_key_node
    return fields


def normalize_project_status(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized not in PROJECT_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_STATUS")
    return normalized


def normalize_stage_status(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized not in STAGE_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_STAGE_STATUS")
    return normalized


def normalize_task_status(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized not in TASK_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TASK_STATUS")
    return normalized


def normalize_priority(value: str | None) -> str:
    normalized = (value or "normal").strip()
    if normalized not in TASK_PRIORITIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TASK_PRIORITY")
    return normalized


def normalize_source_type(value: str | None) -> str:
    normalized = (value or "manual").strip()
    if normalized not in TASK_SOURCES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TASK_SOURCE")
    return normalized


def normalize_evidence_type(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized not in TASK_EVIDENCE_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="INVALID_PROJECT_TASK_EVIDENCE_TYPE")
    return normalized


def can_access_project_progress(user: User) -> bool:
    return has_any_role(
        user,
        {"system_admin", "admin", "staff", "manager", "viewer", "project_viewer", "project_member", "project_manager"},
    )


def can_view_all_projects(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "manager", "viewer", "project_manager"})


def can_manage_project_module(user: User) -> bool:
    return has_any_role(user, {"system_admin", "admin", "manager", "project_manager"})


def can_manage_project(user: User, project: Project) -> bool:
    return can_manage_project_module(user) or project.project_manager_id == user.id


def require_project_access(user: User) -> None:
    if not can_access_project_progress(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def require_project_manager(user: User, project: Project | None = None) -> None:
    if project is None:
        if not can_manage_project_module(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")
        return
    if not can_manage_project(user, project):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def non_budget_project_clause(project_id_column):
    """Keep budget workspaces out of the independent project-progress domain."""

    return ~exists().where(BudgetProjectProfile.project_id == project_id_column)


def accessible_project_query(db: Session, user: User):
    require_project_access(user)
    query = db.query(Project).filter(non_budget_project_clause(Project.id))
    if can_view_all_projects(user):
        return query
    owned_task_project_ids = db.query(ProjectTask.project_id).filter(ProjectTask.owner_user_id == user.id)
    return query.filter(
        or_(
            Project.project_manager_id == user.id,
            Project.created_by == user.id,
            Project.id.in_(owned_task_project_ids),
        )
    )


def get_accessible_project(db: Session, project_id: int, user: User) -> Project:
    project = accessible_project_query(db, user).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_NOT_FOUND")
    return project


def get_project_stage(db: Session, stage_id: int, user: User) -> ProjectStage:
    stage = db.query(ProjectStage).filter(ProjectStage.id == stage_id).first()
    if not stage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_STAGE_NOT_FOUND")
    get_accessible_project(db, stage.project_id, user)
    return stage


def get_accessible_task(db: Session, task_id: int, user: User) -> ProjectTask:
    task = db.query(ProjectTask).filter(ProjectTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROJECT_TASK_NOT_FOUND")
    get_accessible_project(db, task.project_id, user)
    return task


def can_mutate_task(user: User, task: ProjectTask) -> bool:
    return can_manage_project(user, task.project) or task.owner_user_id == user.id


def require_task_mutation(user: User, task: ProjectTask) -> None:
    if not can_mutate_task(user, task):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


def is_task_overdue(task: ProjectTask, *, now: datetime | None = None) -> bool:
    if task.status in TASK_TERMINAL_STATUSES:
        return False
    due_at = to_local_naive(task.due_at)
    if due_at is None:
        return False
    return due_at < (now or now_local_naive())


def is_task_warning(task: ProjectTask, *, now: datetime | None = None) -> bool:
    if task.status in TASK_TERMINAL_STATUSES or task.status == "blocked":
        return False
    due_at = to_local_naive(task.due_at)
    if due_at is None:
        return False
    now = now or now_local_naive()
    return now <= due_at <= now + timedelta(days=2) and int(task.progress_percent or 0) < 80


def progress_status(progress: int) -> str:
    progress = max(0, min(100, int(progress or 0)))
    if progress >= 100:
        return "done"
    if progress >= 80:
        return "submitted"
    if progress >= 50:
        return "progressing"
    if progress >= 25:
        return "started"
    return "todo"


def stage_progress_from_status(stage: ProjectStage) -> int:
    return TASK_PROGRESS_BY_STATUS.get(stage.status, 0)


def task_snapshot(task: ProjectTask) -> dict:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "stage_id": task.stage_id,
        "title": task.title,
        "description": task.description,
        "evidence_requirement": task.evidence_requirement,
        "evidence_policy": task.evidence_policy,
        "is_key_node": bool(task.is_key_node),
        "owner_user_id": task.owner_user_id,
        "owner_role": task.owner_role,
        "collaborators": decode_json_list(task.collaborators_json),
        "status": task.status,
        "progress_percent": task.progress_percent,
        "priority": task.priority,
        "planned_start_at": format_dt(task.planned_start_at),
        "due_at": format_dt(task.due_at),
        "completed_at": format_dt(task.completed_at),
        "blocked_reason": task.blocked_reason,
        "next_action": task.next_action,
        "last_status_before_blocked": task.last_status_before_blocked,
        "source_type": task.source_type,
        "source_id": task.source_id,
    }


def active_task_evidences(task: ProjectTask) -> list[ProjectTaskEvidence]:
    return [evidence for evidence in task.evidences if evidence.status == "active"]


def task_evidence_requirement(task: ProjectTask) -> str | None:
    explicit_requirement = clean_text(getattr(task, "evidence_requirement", None), 2000)
    if explicit_requirement:
        return explicit_requirement
    if getattr(task, "source_id", None) not in {QISHENG_EPC_COMPACT_TEMPLATE_KEY, QISHENG_EPC_FULL_TEMPLATE_KEY}:
        return None
    epc_meta = parse_epc_task_meta(task.description)
    if epc_meta and epc_meta.get("deliverable"):
        return clean_text(epc_meta.get("deliverable"), 2000)
    return None


def task_evidence_summary(task: ProjectTask) -> dict:
    evidences = active_task_evidences(task)
    return {
        "requirement": task_evidence_requirement(task),
        "evidence_count": len(evidences),
        "has_evidence": bool(evidences),
        "has_file": any(evidence.evidence_type == "file" for evidence in evidences),
        "has_link": any(evidence.evidence_type == "link" for evidence in evidences),
        "has_text": any(evidence.evidence_type == "text" for evidence in evidences),
    }


def project_evidence_summary(project: Project) -> dict:
    active_tasks = [task for task in project.tasks if task.status != "cancelled"]
    required_tasks = [task for task in active_tasks if task_evidence_requirement(task)]
    evidenced_tasks = [task for task in required_tasks if active_task_evidences(task)]
    missing_tasks = [task for task in required_tasks if not active_task_evidences(task)]
    done_without_evidence = [task for task in missing_tasks if task.status == "done"]
    evidence_completion_percent = int(round(len(evidenced_tasks) * 100 / len(required_tasks))) if required_tasks else 0
    return {
        "required_task_count": len(required_tasks),
        "evidenced_task_count": len(evidenced_tasks),
        "missing_evidence_task_count": len(missing_tasks),
        "done_without_evidence_task_count": len(done_without_evidence),
        "open_missing_evidence_task_count": len([task for task in missing_tasks if task.status != "done"]),
        "evidence_completion_percent": evidence_completion_percent,
    }


def backfill_project_task_evidence_fields(db: Session, *, apply: bool = False, project_id: int | None = None) -> dict:
    summary = {
        "apply": apply,
        "project_id": project_id,
        "total_task_count": 0,
        "updated_task_count": 0,
        "epc_compact_task_count": 0,
        "epc_full_task_count": 0,
        "single_user_task_count": 0,
        "manual_or_other_task_count": 0,
        "key_node_count": 0,
        "requirement_count": 0,
        "soft_reminder_count": 0,
        "none_count": 0,
        "complete_required_count": 0,
        "a_level_candidate_count": 0,
        "template_match_missing_count": 0,
        "requirement_parse_missing_count": 0,
        "template_match_missing_task_ids": [],
        "requirement_parse_missing_task_ids": [],
    }
    query = db.query(ProjectTask).filter(non_budget_project_clause(ProjectTask.project_id))
    if project_id is not None:
        query = query.filter(ProjectTask.project_id == project_id)
    tasks = query.order_by(ProjectTask.id.asc()).all()
    for task in tasks:
        summary["total_task_count"] += 1
        stage_key = task.stage.stage_key if task.stage else None
        source_id = task.source_id
        if source_id == QISHENG_EPC_COMPACT_TEMPLATE_KEY:
            summary["epc_compact_task_count"] += 1
        elif source_id == QISHENG_EPC_FULL_TEMPLATE_KEY:
            summary["epc_full_task_count"] += 1
        elif source_id == SINGLE_USER_TRIAL_TEMPLATE_KEY:
            summary["single_user_task_count"] += 1
        else:
            summary["manual_or_other_task_count"] += 1

        template_item = qisheng_epc_template_item(stage_key, task.title)
        if source_id in {QISHENG_EPC_COMPACT_TEMPLATE_KEY, QISHENG_EPC_FULL_TEMPLATE_KEY} and template_item is None:
            summary["template_match_missing_count"] += 1
            if len(summary["template_match_missing_task_ids"]) < 50:
                summary["template_match_missing_task_ids"].append(task.id)
        if template_item and (template_item["stage_key"], template_item["node"]) in QISHENG_EPC_A_LEVEL_GATE_NODE_KEYS:
            summary["a_level_candidate_count"] += 1

        fields = derive_task_evidence_fields(
            source_id=source_id,
            stage_key=stage_key,
            title=task.title,
            description=task.description,
            epc_item=template_item,
            requirement_source="description",
        )
        if (
            source_id in {QISHENG_EPC_COMPACT_TEMPLATE_KEY, QISHENG_EPC_FULL_TEMPLATE_KEY}
            and template_item
            and clean_text(template_item.get("deliverable"), 2000)
            and not fields["evidence_requirement"]
        ):
            summary["requirement_parse_missing_count"] += 1
            if len(summary["requirement_parse_missing_task_ids"]) < 50:
                summary["requirement_parse_missing_task_ids"].append(task.id)

        if fields["is_key_node"]:
            summary["key_node_count"] += 1
        if fields["evidence_requirement"]:
            summary["requirement_count"] += 1
        if fields["evidence_policy"] == "soft_reminder":
            summary["soft_reminder_count"] += 1
        elif fields["evidence_policy"] == "complete_required":
            summary["complete_required_count"] += 1
        else:
            summary["none_count"] += 1

        changed = (
            clean_text(task.evidence_requirement, 2000) != fields["evidence_requirement"]
            or (task.evidence_policy or "none") != fields["evidence_policy"]
            or bool(task.is_key_node) != bool(fields["is_key_node"])
        )
        if changed:
            summary["updated_task_count"] += 1
            if apply:
                task.evidence_requirement = fields["evidence_requirement"]
                task.evidence_policy = fields["evidence_policy"]
                task.is_key_node = bool(fields["is_key_node"])
    if apply:
        db.commit()
    return summary


def stage_snapshot(stage: ProjectStage) -> dict:
    return {
        "id": stage.id,
        "project_id": stage.project_id,
        "stage_key": stage.stage_key,
        "stage_name": stage.stage_name,
        "sort_order": stage.sort_order,
        "weight_percent": stage.weight_percent,
        "status": stage.status,
        "progress_percent": stage.progress_percent,
        "owner_role": stage.owner_role,
        "owner_user_id": stage.owner_user_id,
        "planned_start_at": format_dt(stage.planned_start_at),
        "planned_finish_at": format_dt(stage.planned_finish_at),
        "actual_start_at": format_dt(stage.actual_start_at),
        "actual_finish_at": format_dt(stage.actual_finish_at),
    }


def project_snapshot(project: Project) -> dict:
    return {
        "id": project.id,
        "project_code": project.project_code,
        "name": project.name,
        "client_name": project.client_name,
        "address": project.address,
        "description": project.description,
        "status": project.status,
        "risk_level": project.risk_level,
        "progress_percent": project.progress_percent,
        "project_manager_id": project.project_manager_id,
        "owner_department": project.owner_department,
        "planned_start_at": format_dt(project.planned_start_at),
        "planned_finish_at": format_dt(project.planned_finish_at),
        "actual_start_at": format_dt(project.actual_start_at),
        "actual_finish_at": format_dt(project.actual_finish_at),
    }


def decode_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def encode_json_list(value: list | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def write_project_event(
    db: Session,
    *,
    project: Project,
    event_type: str,
    actor: User | None,
    stage: ProjectStage | None = None,
    task: ProjectTask | None = None,
    before: dict | None = None,
    after: dict | None = None,
    message: str | None = None,
    payload: dict | None = None,
) -> ProjectTaskEvent:
    event_payload = dict(payload or {})
    if before is not None:
        event_payload["before"] = before
    if after is not None:
        event_payload["after"] = after
    event = ProjectTaskEvent(
        project_id=project.id,
        stage_id=stage.id if stage else task.stage_id if task else None,
        task_id=task.id if task else None,
        event_type=event_type,
        from_status=before.get("status") if before else None,
        to_status=after.get("status") if after else None,
        message=clean_text(message, 2000),
        actor_user_id=actor.id if actor else None,
        payload_json=json.dumps(event_payload, ensure_ascii=False, default=str) if event_payload else None,
    )
    db.add(event)
    return event


def create_default_stages(db: Session, project: Project) -> list[ProjectStage]:
    stages: list[ProjectStage] = []
    for index, item in enumerate(DEFAULT_PROJECT_STAGES, start=1):
        stage = ProjectStage(
            project_id=project.id,
            stage_key=item["key"],
            stage_name=item["name"],
            sort_order=index,
            weight_percent=item["weight"],
            owner_role=item["owner_role"],
            status="todo",
            progress_percent=0,
        )
        db.add(stage)
        stages.append(stage)
    return stages


def create_qisheng_epc_stages(db: Session, project: Project, *, owner: User | None = None) -> list[ProjectStage]:
    stages: list[ProjectStage] = []
    for index, item in enumerate(QISHENG_EPC_STAGE_DEFINITIONS, start=1):
        stage = ProjectStage(
            project_id=project.id,
            stage_key=item["key"],
            stage_name=item["name"],
            sort_order=index,
            weight_percent=item["weight"],
            owner_role=item["owner_role"],
            owner_user_id=owner.id if owner else None,
            status="todo",
            progress_percent=0,
        )
        db.add(stage)
        stages.append(stage)
    return stages


def _scaled_template_datetime(project: Project, due_day: int) -> datetime | None:
    start = to_local_naive(project.planned_start_at)
    finish = to_local_naive(project.planned_finish_at)
    if start is None:
        return None
    if finish is None or finish <= start:
        finish = start + timedelta(days=SINGLE_USER_TRIAL_TEMPLATE_DAYS)
    total_seconds = max((finish - start).total_seconds(), 1)
    ratio = max(0, min(1, due_day / SINGLE_USER_TRIAL_TEMPLATE_DAYS))
    return start + timedelta(seconds=round(total_seconds * ratio))


def _scaled_epc_datetime(project: Project, position: int, total: int) -> datetime | None:
    start = to_local_naive(project.planned_start_at)
    finish = to_local_naive(project.planned_finish_at)
    if start is None:
        return None
    if finish is None or finish <= start:
        finish = start + timedelta(days=QISHENG_EPC_TEMPLATE_DAYS)
    total_seconds = max((finish - start).total_seconds(), 1)
    ratio = max(0, min(1, position / max(total, 1)))
    return start + timedelta(seconds=round(total_seconds * ratio))


def create_single_user_trial_tasks(db: Session, project: Project, *, owner: User, actor: User) -> list[ProjectTask]:
    stages_by_key = {stage.stage_key: stage for stage in project.stages}
    tasks: list[ProjectTask] = []
    for stage in project.stages:
        stage.owner_user_id = owner.id
    for item in SINGLE_USER_TRIAL_TASKS:
        stage = stages_by_key.get(item["stage_key"])
        if stage is None:
            continue
        evidence_fields = derive_task_evidence_fields(source_id=SINGLE_USER_TRIAL_TEMPLATE_KEY)
        task = ProjectTask(
            project_id=project.id,
            stage_id=stage.id,
            title=clean_text(item["title"], 255) or item["title"],
            description=clean_text(item.get("description"), 4000),
            **evidence_fields,
            owner_user_id=owner.id,
            owner_role=stage.owner_role,
            collaborators_json=None,
            status="todo",
            progress_percent=0,
            priority=normalize_priority(item.get("priority")),
            planned_start_at=None,
            due_at=_scaled_template_datetime(project, int(item.get("due_day") or 0)),
            next_action=clean_text(item.get("next_action"), 2000),
            source_type="system",
            source_id=SINGLE_USER_TRIAL_TEMPLATE_KEY,
            created_by=actor.id,
        )
        db.add(task)
        db.flush()
        tasks.append(task)
        write_project_event(
            db,
            project=project,
            stage=stage,
            task=task,
            event_type="task_created",
            actor=actor,
            after=task_snapshot(task),
            message="创建单人试运行模板任务",
        )
    recalculate_project_progress(db, project)
    return tasks


def create_qisheng_epc_tasks(db: Session, project: Project, *, owner: User, actor: User, mode: str | None = "compact") -> list[ProjectTask]:
    stages_by_key = {stage.stage_key: stage for stage in project.stages}
    template_key = qisheng_epc_template_key(mode)
    template_tasks = qisheng_epc_template_tasks(mode)
    tasks: list[ProjectTask] = []
    for index, item in enumerate(template_tasks, start=1):
        stage = stages_by_key.get(item["stage_key"])
        if stage is None:
            continue
        evidence_fields = derive_task_evidence_fields(
            source_id=template_key,
            stage_key=item["stage_key"],
            title=item["node"],
            epc_item=item,
            complete_required_enabled=True,
        )
        task = ProjectTask(
            project_id=project.id,
            stage_id=stage.id,
            title=clean_text(item["node"], 255) or item["node"],
            description=clean_text(epc_task_description(item), 4000),
            **evidence_fields,
            owner_user_id=owner.id,
            owner_role=clean_text(item["lead_department"], 64),
            collaborators_json=None,
            status="todo",
            progress_percent=0,
            priority="normal",
            planned_start_at=None,
            due_at=_scaled_epc_datetime(project, index, len(template_tasks)),
            next_action=clean_text(f"准备成果文件：{item['deliverable']}", 2000),
            source_type="system",
            source_id=template_key,
            created_by=actor.id,
        )
        db.add(task)
        db.flush()
        tasks.append(task)
        write_project_event(
            db,
            project=project,
            stage=stage,
            task=task,
            event_type="task_created",
            actor=actor,
            after=task_snapshot(task),
            message=f"创建 EPC 流程节点：{item['node']}",
        )
    recalculate_project_progress(db, project)
    return tasks


def activate_project_task_hard_gates(db: Session, *, apply: bool = False, project_id: int | None = None) -> dict:
    summary = {
        "apply": apply,
        "project_id": project_id,
        "total_task_count": 0,
        "a_level_candidate_count": 0,
        "updated_task_count": 0,
        "complete_required_count": 0,
        "soft_reminder_count": 0,
        "none_count": 0,
        "template_match_missing_count": 0,
        "template_match_missing_task_ids": [],
    }
    query = db.query(ProjectTask).filter(non_budget_project_clause(ProjectTask.project_id))
    if project_id is not None:
        query = query.filter(ProjectTask.project_id == project_id)
    tasks = query.order_by(ProjectTask.id.asc()).all()
    for task in tasks:
        summary["total_task_count"] += 1
        stage_key = task.stage.stage_key if task.stage else None
        template_item = qisheng_epc_template_item(stage_key, task.title)
        if task.source_id in {QISHENG_EPC_COMPACT_TEMPLATE_KEY, QISHENG_EPC_FULL_TEMPLATE_KEY} and template_item is None:
            summary["template_match_missing_count"] += 1
            if len(summary["template_match_missing_task_ids"]) < 50:
                summary["template_match_missing_task_ids"].append(task.id)

        if is_qisheng_epc_a_level_gate_item(template_item):
            summary["a_level_candidate_count"] += 1

        fields = derive_task_evidence_fields(
            source_id=task.source_id,
            stage_key=stage_key,
            title=task.title,
            description=task.description,
            epc_item=template_item,
            requirement_source="description",
            complete_required_enabled=True,
        )
        if fields["evidence_policy"] == "complete_required":
            summary["complete_required_count"] += 1
        elif fields["evidence_policy"] == "soft_reminder":
            summary["soft_reminder_count"] += 1
        else:
            summary["none_count"] += 1

        changed = (
            clean_text(task.evidence_requirement, 2000) != fields["evidence_requirement"]
            or (task.evidence_policy or "none") != fields["evidence_policy"]
            or bool(task.is_key_node) != bool(fields["is_key_node"])
        )
        if changed:
            summary["updated_task_count"] += 1
            if apply:
                task.evidence_requirement = fields["evidence_requirement"]
                task.evidence_policy = fields["evidence_policy"]
                task.is_key_node = bool(fields["is_key_node"])
    if apply:
        db.commit()
    return summary


def next_project_code(db: Session) -> str:
    prefix = f"PRJ-{now_local_naive().strftime('%Y%m%d')}"
    count = db.query(Project).filter(Project.project_code.like(f"{prefix}-%")).count() + 1
    return f"{prefix}-{count:03d}"


def recalculate_project_progress(db: Session, project: Project) -> Project:
    db.flush()
    now = now_local_naive()
    stages = sorted(project.stages, key=lambda item: item.sort_order or 0)
    for stage in stages:
        active_tasks = [task for task in stage.tasks if task.status != "cancelled"]
        if active_tasks:
            stage.progress_percent = int(round(sum(int(task.progress_percent or 0) for task in active_tasks) / len(active_tasks)))
            stage.status = progress_status(stage.progress_percent)
            if stage.progress_percent > 0 and stage.actual_start_at is None:
                stage.actual_start_at = now
            if stage.progress_percent >= 100 and stage.actual_finish_at is None:
                stage.actual_finish_at = now
        else:
            stage.progress_percent = stage_progress_from_status(stage)
            if stage.status == "done" and stage.actual_finish_at is None:
                stage.actual_finish_at = now

    total_weight = sum(max(0, int(stage.weight_percent or 0)) for stage in stages)
    if total_weight > 0:
        project.progress_percent = int(
            round(sum(max(0, int(stage.weight_percent or 0)) * int(stage.progress_percent or 0) for stage in stages) / total_weight)
        )
    else:
        project.progress_percent = 0

    active_tasks = [task for task in project.tasks if task.status != "cancelled"]
    if project.status in {"completed", "cancelled"}:
        project.risk_level = "normal"
    elif any(task.status == "blocked" for task in active_tasks):
        project.risk_level = "blocked"
    elif any(is_task_overdue(task, now=now) for task in active_tasks):
        project.risk_level = "delayed"
    elif any(is_task_warning(task, now=now) for task in active_tasks):
        project.risk_level = "warning"
    else:
        project.risk_level = "normal"
    return project


def current_stage(project: Project) -> ProjectStage | None:
    stages = sorted(project.stages, key=lambda item: item.sort_order or 0)
    for stage in stages:
        if int(stage.progress_percent or 0) < 100:
            return stage
    return stages[-1] if stages else None


def serialize_project(project: Project, *, users: dict[int, User] | None = None, include_stages: bool = False) -> dict:
    users = users or {}
    current = current_stage(project)
    active_tasks = [task for task in project.tasks if task.status != "cancelled"]
    data = {
        **project_snapshot(project),
        "created_at": format_dt(project.created_at),
        "updated_at": format_dt(project.updated_at),
        "project_manager_username": users.get(project.project_manager_id).username if users.get(project.project_manager_id) else None,
        "current_stage_id": current.id if current else None,
        "current_stage_key": current.stage_key if current else None,
        "current_stage_name": current.stage_name if current else None,
        "task_count": len(active_tasks),
        "done_task_count": sum(1 for task in active_tasks if task.status == "done"),
        "blocked_task_count": sum(1 for task in active_tasks if task.status == "blocked"),
        "overdue_task_count": sum(1 for task in active_tasks if is_task_overdue(task)),
        "evidence_summary": project_evidence_summary(project),
    }
    if include_stages:
        data["stages"] = [serialize_stage(stage, users=users) for stage in sorted(project.stages, key=lambda item: item.sort_order or 0)]
    return data


def serialize_stage(stage: ProjectStage, *, users: dict[int, User] | None = None) -> dict:
    users = users or {}
    data = stage_snapshot(stage)
    data["owner_username"] = users.get(stage.owner_user_id).username if stage.owner_user_id and users.get(stage.owner_user_id) else None
    data["task_count"] = len([task for task in stage.tasks if task.status != "cancelled"])
    data["blocked_task_count"] = len([task for task in stage.tasks if task.status == "blocked"])
    return data


def serialize_evidence(evidence: ProjectTaskEvidence, *, users: dict[int, User] | None = None, files: dict[str, object] | None = None) -> dict:
    users = users or {}
    files = files or {}
    file_obj = files.get(evidence.file_object_id) if evidence.file_object_id else None
    data = {
        "id": evidence.id,
        "project_id": evidence.project_id,
        "stage_id": evidence.stage_id,
        "task_id": evidence.task_id,
        "evidence_type": evidence.evidence_type,
        "title": evidence.title,
        "description": evidence.description,
        "file_object_id": evidence.file_object_id,
        "external_url": evidence.external_url,
        "external_provider": evidence.external_provider,
        "requirement_snapshot": evidence.requirement_snapshot,
        "status": evidence.status,
        "created_by": evidence.created_by,
        "created_by_username": users.get(evidence.created_by).username if users.get(evidence.created_by) else None,
        "created_at": format_dt(evidence.created_at),
        "updated_at": format_dt(evidence.updated_at),
        "removed_by": evidence.removed_by,
        "removed_by_username": users.get(evidence.removed_by).username if evidence.removed_by and users.get(evidence.removed_by) else None,
        "removed_at": format_dt(evidence.removed_at),
        "remove_reason": evidence.remove_reason,
    }
    if file_obj:
        data.update(
            {
                "file_original_filename": getattr(file_obj, "original_filename", None),
                "file_content_type": getattr(file_obj, "content_type", None),
                "file_size_bytes": getattr(file_obj, "size_bytes", None),
            }
        )
    return data


def serialize_task(task: ProjectTask, *, users: dict[int, User] | None = None) -> dict:
    users = users or {}
    data = task_snapshot(task)
    epc_meta = parse_epc_task_meta(task.description)
    evidence_summary = task_evidence_summary(task)
    data.update(
        {
            "created_at": format_dt(task.created_at),
            "updated_at": format_dt(task.updated_at),
            "project_name": task.project.name if task.project else None,
            "stage_name": task.stage.stage_name if task.stage else None,
            "owner_username": users.get(task.owner_user_id).username if users.get(task.owner_user_id) else None,
            "created_by": task.created_by,
            "is_overdue": is_task_overdue(task),
            "epc_meta": epc_meta,
            "evidence_summary": evidence_summary,
            "evidence_requirement": evidence_summary.get("requirement"),
            "evidence_count": evidence_summary.get("evidence_count", 0),
            "has_evidence": evidence_summary.get("has_evidence", False),
        }
    )
    if epc_meta:
        data.update(
            {
                "epc_content": epc_meta.get("content"),
                "epc_standard": epc_meta.get("standard"),
                "epc_assist_department": epc_meta.get("assist_department"),
                "epc_supervise_department": epc_meta.get("supervise_department"),
                "epc_deliverable": epc_meta.get("deliverable"),
            }
        )
    return data


def serialize_event(event: ProjectTaskEvent, *, users: dict[int, User] | None = None) -> dict:
    users = users or {}
    return {
        "id": event.id,
        "project_id": event.project_id,
        "stage_id": event.stage_id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "message": event.message,
        "actor_user_id": event.actor_user_id,
        "actor_username": users.get(event.actor_user_id).username if event.actor_user_id and users.get(event.actor_user_id) else None,
        "payload_json": event.payload_json,
        "created_at": format_dt(event.created_at),
    }
