# 旗胜投标机会研判 Agent PDF-C1 结构化 Chunk 协议

版本：v0.1-r34

日期：2026-08-14

状态：代码与本地隔离专项验证完成；尚未运行真实 PDF、OCR、视觉或模型调用

## 1. 目标与边界

PDF-C1 冻结并实现正文结构块到检索证据的确定性转换，不读取 PDF 文件本身。输入是未来 PDF-C2 结构解析器产生的有序 Block；输出是 Phase 2 可持久化的三层 EvidenceFragment：

```text
section_parent
  -> retrieval_child
       -> evidence_atom
```

本增量不连接外部环境，不调用 OCR、视觉、模型、MCP 或真实对象存储，不修改旧 `bid_intake_*`，不改变当前 ParseHead，也不应用到 ECS。

## 2. 权威角色

| 角色 | 父角色 | 检索职责 | 事实引用 |
|---|---|---|---|
| `section_parent` | 无 | 章节级辅助召回和上下文 | 禁止 |
| `retrieval_child` | `section_parent` | BM25/向量/重排主要候选 | 禁止 |
| `evidence_atom` | `retrieval_child` | `evidence.read` 精确回源 | 仅其可引用 |

`context_prefix`、`retrieval_text`、Parent 文本和 Child 拼接文本均是确定性检索派生物，不是事实证据。只有由 `evidence.read` 返回且 `context_read=true` 的 Atom 才能进入 Fact、Claim 和 Citation。

## 3. 输入 Block

每个输入块必须包含：

- 唯一 `block_key`；
- 非空规范化文本；
- `block_type`；
- 正整数 `page_no`；
- Run 内唯一非负 `ordinal`；
- 可空 `section_path`、`bbox`；
- 可选 `boundary_before/boundary_after`。

首版 Block 类型冻结为：

```text
heading | paragraph | clause | list_item
table | table_row | form_field | image | caption | attachment_boundary
```

PDF-C1 不推断业务标段、资料角色、资格结论或风险结论。

## 4. Token 估算

首版使用 `bid-token-estimator-cjk-conservative-v1`，不加载任何模型 Tokenizer：

- 每个 CJK 字符计 1；
- ASCII 字母数字连续串每 4 字符计 1；
- 非空白标点计 1；
- 结果称为 `estimated_tokens`，不得伪装为模型实际 Token。

该算法是确定性安全估算。未来切换真实 Embedding Tokenizer 必须升级 `token_estimator_version` 和 Chunk Profile，不能在同版本内静默改变边界。

## 5. Chunk Profile

机器 Profile：`contracts/bid_assessment/v1/pdf-c1-chunk-profile.json`。

```text
soft_min_tokens = 220
target_tokens   = 380
soft_max_tokens = 500
hard_max_tokens = 600
long_overlap    = 80
```

规则：

1. 章节变化是硬边界；普通换页不是硬边界。
2. 正常结构块之间 overlap 为 0。
3. Clause、表格、表格行、表单字段、图片、题注和附件边界独立成 Child。
4. 同一章节的短 Paragraph/List Item 依阅读顺序合并。
5. 单 Block 超过 600 estimated tokens 时才滑窗切分。
6. 超长块优先在 220—500 Token 之间最近的句末或换行切分；否则寻找 600 Token 内下一自然边界；再否则在 600 Token 硬切。
7. 超长切片从第二片开始保留最多 80 Token 左侧 overlap，并显式写入 `overlap_left_tokens`。

## 6. 稳定身份与 Hash

所有 Key 均由 canonical JSON SHA-256 生成：

- Parent：合同/Profile、章节路径、章节 occurrence、首个 Block Key；
- Child：Parent Key、Child 序号、来源 Block 与字符 Span；
- Atom：Profile、来源 Block、字符 Span、文本 Hash。

结果同时提供 `text_hash`、`locator_hash`、`retrieval_hash` 和整体 `result_hash`。同输入、同 Profile 必须产生相同结构、边界、Key 和 Hash。

数据库完成事务仍可生成物理 UUID；逻辑 Key 和完整 `DocumentParseResult` Hash 必须保持可复现。

## 7. 上下文索引

每个 Child 的 `retrieval_text` 为：

```text
[文档] canonical document label（可空）
[章节] section path
[页码] page range
[类型] block type

normalized child text
```

首版禁止逐 Chunk 模型摘要。Parent 只保存标题/章节路径形成抽取式索引。模型生成 Parent Summary 必须作为后续独立派生产物版本化，且永远不可引用。

## 8. 当前存储与迁移门禁

PDF-C1 复用 `bid_evidence_fragments.parent_id` 和 `locator_json`：

- Parent 无父节点；
- Child 指向 Parent；
- Atom 指向 Child；
- `fragment_role`、Block 类型、页范围、Span、Token、可引用标记写入 locator；
- `normalized_text` 保持不可变。

本增量不新增 Alembic revision，代码唯一 head 保持 `20260813_0101`。如果 PDF-C2/C3 需要数据库按角色索引、第一类 Retrieval Index Head 或多对多 Chunk→Atom 血缘，必须先冻结独立协议，再评估线性 `0102`；不得依赖启动时建表或把迁移应用到 ECS。

## 9. PDF-C2/C3 接口边界

PDF-C2 只负责产生带 bbox、阅读顺序和章节路径的 `StructuredEvidenceBlock`，再调用 PDF-C1 Builder；不得复制另一套切 Chunk 逻辑。

PDF-C3 Evidence MCP 将：

- 新 Profile 只检索 `retrieval_child`；
- Parent 只进入辅助通道；
- `evidence.read(source_atoms)` 将 Child 展开为直接 Atom；
- 邻居只在同一 Parent 下扩展；
- 旧 Profile 缺失 `fragment_role` 时继续走现有兼容路径。

## 10. 验收门

专项测试必须覆盖：

- 合同 JSON 与 Schema；
- Token 估算稳定性；
- 正文合并和章节硬边界；
- Clause/表格隔离；
- 超长切分、80 Token overlap 和 600 Token 硬上限；
- Parent/Child/Atom 角色与可引用约束；
- 相同输入重复运行 Key/Hash 一致；
- 所有非 Heading 原文 Span 被覆盖，只有声明 overlap 的范围重复。

上述 Agent 专项测试已在用户明确授权后执行。验证结果：

- PDF-C1 合同、Token、边界、overlap、Parent-Child-Atom、Span 覆盖和稳定 Hash：`12 passed`；
- Phase 2 Parse Worker 结果合同相邻回归：`4 passed`；
- 合计：`16 passed / 0 failed`。

验证只使用合成结构块和隔离测试数据库，未读取真实 PDF，未调用 OCR、视觉、模型、MCP、外部 Tool 或真实对象存储，未连接外部环境。
