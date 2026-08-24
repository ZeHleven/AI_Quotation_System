# 旗胜投标机会研判 Agent PDF-C2 原生布局解析协议

版本：v0.1-r35

日期：2026-08-14

状态：代码与本地隔离专项验证完成；未运行真实 PDF、OCR、视觉、模型或 MCP 调用

## 1. 目标与边界

PDF-C2 把 PDF 原生文本层和矢量表格层转换为带页码、bbox、阅读顺序、结构类型和章节路径的 `StructuredEvidenceBlock`，随后只调用 PDF-C1 Builder 生成 Section Parent→Retrieval Child→Evidence Atom。

PDF-C2 不执行 OCR、页面视觉理解、模型摘要、业务标段识别、资料角色推断、资格结论或风险结论。原生文本抽取成功不等同于视觉布局已验证；扫描页、原生文本过少页和低覆盖页只标记 `not_requested` 并等待后续独立 OCR/视觉协议。

本增量不连接外部环境，不访问真实 MinIO/Redis，不读取真实 PDF，不修改旧 `bid_intake_*`，不应用到 ECS。

## 2. 版本与启用门禁

机器 Profile：`contracts/bid_assessment/v1/pdf-c2-native-layout-profile.json`。

冻结版本：

```text
layout contract = bid.pdf.native-layout.v1
layout profile  = bid-pdf-native-layout-profile-v1
parser profile  = bid-document-parser-profile-v2-pdf-native-layout
chunk contract  = bid.evidence.chunk.v2
chunk profile   = bid-evidence-chunk-profile-v1
```

旧 `bid-document-parser-profile-v1` 继续走原 Phase 2 兼容解析，历史 Run 不被改写。PDF-C2 必须同时满足：

```text
FEATURE_BID_ASSESSMENT_V1_RUNTIME=true
FEATURE_BID_ASSESSMENT_PHASE2_DOCUMENT_WORKER=true
FEATURE_BID_ASSESSMENT_PDF_C2_NATIVE_LAYOUT=true
BID_DOCUMENT_PARSER_PROFILE_VERSION=bid-document-parser-profile-v2-pdf-native-layout
```

任一依赖缺失即启动配置 fail-closed；PDF-C2 子开关默认 `false`，Parser Profile 默认仍为 v1。

PDF-C2 与 `FEATURE_BID_ASSESSMENT_PHASE4_EVIDENCE_MCP=true` 的组合只有在 PDF-C3 role-aware Retrieval Profile 同时启用时才允许启动，否则 fail-closed，防止 Parent/Child 被旧检索兼容路径当作可引用正文。Phase 2 标段检测已增加兼容证据门：新三层结果只读取 `evidence_atom + is_citable=true`，旧无角色 Evidence 保持原行为。

## 3. 输入和安全限制

权威输入只有对象存储读取后、经文件大小和 SHA-256 校验的 PDF bytes。文件名、MIME 和 `parser_hint` 仅能用于非权威路由，不能进入 Evidence 或影响结构判断。

冻结限制：

| 限制 | 数值 |
|---|---:|
| 最大页数 | 2,000 |
| 单页最大 Word | 50,000 |
| 最大结构块 | 200,000 |
| 最大原生字符 | 1,200,000 |

超过限制为不可重试 `BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED`；损坏、加密不可读、坐标非法或 Hash 不一致 fail-closed，不产生部分权威结果。

## 4. 坐标和页面权威

PDF-C2 使用 `pdfplumber` 的 PDF 原生对象，不渲染页面。bbox 冻结为：

```text
origin = page_top_left
unit   = PDF point
order  = [x0, top, x1, bottom]
precision = 3 decimals
```

每页生成一个 Phase 2 `ParseUnit(unit_type=page)`，记录页宽、高、rotation、原生字符数、抽取字符数、Word/Block/Table/Image 数、覆盖率和阅读顺序模式。页数只能来自实际 PDF page tree。

## 5. 原生布局算法

### 5.1 Word 与 Line

从原生字符层抽取带 `x0/top/x1/bottom/fontname/size` 的 Word；非法或零面积坐标丢弃，非有限坐标使整个解析失败。Word 以 y 容差和字体高度聚合为 Line；CJK 相邻文本不强插空格，ASCII 词保留必要空格。

### 5.2 阅读顺序

默认按页面 top→x0 排序。一个垂直区域同时具备足量左右窄行、且跨中线歧义行不超过冻结比例时，使用确定性的左栏→右栏顺序，并记录 `reading_order_mode=two_column`。矢量表格作为垂直 Band：先处理表格上方文本，再处理表格行，再处理下方文本。

首版不宣称解决任意复杂排版。低置信阅读顺序的真实样例评测属于后续验收门，不能由文件名或业务关键词纠正。

### 5.3 结构分类

结构类型仅使用通用版式信号：字体大小、粗体、居中、通用章节编号、列表符号、几何间距和缩进。

```text
heading | paragraph | clause | list_item | table_row
```

禁止使用“投标、标段、资格、合同”等业务关键词决定 Heading 或 Scope。Heading 维护层级栈并形成 `section_path`；Paragraph 按同页几何连续性合并；Clause/List 保留独立源块身份。

### 5.4 矢量表格

首版只接受 PDF 原生线框可稳定识别、且至少两列的表格，逐行输出 `table_row`。已接受表格 bbox 内的普通 Word 从正文通道排除，避免双重证据。表格检测或抽取失败时保留普通文本通道并写稳定警告，不生成猜测单元格。

图片区域不生成伪造文本 Evidence；只记录 `image_count`，原生文本不足时要求 OCR/人工复核。

## 6. 稳定身份与 Hash

Block Key 由 Layout 合同/Profile、文件 SHA-256、页码、全局 ordinal、Block 类型、三位小数 bbox 和文本 Hash 的 canonical JSON 生成。相同 PDF bytes、相同 Profile 必须产生相同页面指标、Block 顺序、section path、Block Key 和 `result_hash`。

PDF-C2 不使用文件名、上传顺序、数据库 UUID 或运行时间生成逻辑身份。

## 7. PDF-C1 接入

`bid_document_parser_adapter.py` 只有在冻结 v2 Parser Profile 和 PDF-C2 开关同时启用时执行：

```text
PDF bytes
  -> PDF-C2 Native Layout
  -> StructuredEvidenceBlock[]
  -> PDF-C1 build_evidence_chunks()
  -> Phase 2 DocumentParseResult
```

禁止在 PDF-C2 内复制另一套 Chunk 算法。PDF-C1 输出映射为现有 `EvidenceFragmentResult`：

- Parent/Child/Atom 的逻辑 Key 保持不变；
- `parent_key` 映射到既有 `bid_evidence_fragments.parent_id`；
- `fragment_role/is_citable`、Layout/Chunk/Profile/Hash 血缘写入 `locator_json`；
- Parent/Child 不可引用，只有 Atom 可引用；
- Fragment 绑定其 `page_no` 对应的 Page ParseUnit，跨页范围继续由 locator 的 `page_end` 表示。

## 8. 质量、OCR 和失败边界

逐页状态：

- 有稳定原生结构且无警告：`succeeded/native/not_applicable`；
- 无原生文本：`partial/none/not_requested`；
- 图像页原生字符少于 20 或结构覆盖率低于 0.65：`partial/native/not_requested`；
- 结构检测警告但无需 OCR：`partial/native/not_applicable`。

全文件完全无可引用原生 Block 时返回不可重试 `BID_DOCUMENT_OCR_REQUIRED`，不得把空页、图片占位文字或 MIME 当正文。混合 PDF 保留有原生文本页，Run 为 `partial` 并显式列出待 OCR 页。

## 9. 数据库与迁移结论

PDF-C2 复用 `20260811_0092` 已有 ParseRun、ParseUnit 和 EvidenceFragment 结构，以及 PDF-C1 已冻结的 `parent_id/locator_json` 映射。不需要角色索引、新权威表或新受约束枚举，因此不新增 Alembic revision，代码唯一 head 保持 `20260813_0101`。

后续 PDF-C3 已冻结独立协议，并以线性 `20260814_0102` 新增 RetrievalIndex/Entry/Head；该变化不回写 PDF-C2 的历史迁移结论。任何 Agent 迁移仍不得应用到 ECS。

## 10. 本地隔离验收结果

2026-08-14 经用户明确授权，专项验证覆盖：

- Profile JSON、Layout Schema 和配置依赖 fail-closed；
- PDF 魔数、SHA-256、页数/字符/Word/Block 限制；
- Word→Line→Paragraph、Heading 层级与 section path；
- bbox 坐标、页旋转、稳定阅读顺序和双栏顺序；
- 矢量表格行抽取与正文去重；
- 空白/扫描页、混合 PDF、低覆盖页和 OCR 状态；
- PDF-C2→PDF-C1→Parent/Child/Atom→Phase 2 Result 映射；
- 相同 bytes/Profile 的 Block Key、Locator 和 Hash 稳定；
- 旧 Parser Profile 兼容、Document Worker 结果合同及事务回滚相邻回归。

结果：

- PDF-C2 合同、配置门禁、合成原生布局、bbox/旋转/双栏/矢量表格、空白与混合页/OCR 状态、稳定 Hash、PDF-C2→PDF-C1→Phase 2 映射及 Lot Atom 证据门：`13 passed`；
- 机器合同、Schema 清单与统一错误目录：`74 passed`；
- Phase 2 Parse/Lot Worker 相邻回归：`4 passed`；
- 合并复跑：`91 passed / 0 failed`。

本次只由测试在内存生成并解析合成 PDF，未读取或渲染真实 PDF，未调用 OCR、视觉、模型、MCP、外部 Tool 或真实对象存储，未连接 ECS/CentOS/真实 MinIO/Redis。后续运行真实样例、OCR/视觉、模型或外部调用仍须重新取得用户明确授权。
