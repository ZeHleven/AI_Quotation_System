# 报价资料研判 Agent Phase 1：招标资料 MCP Server

## 本阶段结论

Phase 1 已把 Phase 0 的假证据客户端替换边界实现为一个可独立启动、可通过官方 MCP 客户端调用的只读服务。当前实现用于验证协议、安全和 Agent 集成，不代表资料入库、OCR、MySQL 或 Milvus 已经接通。

已完成：

- MCP Resource：`tender://current/manifest`
- ReAct 可见 Tool：
  - `search_tender_evidence`
  - `read_evidence_context`
  - `compare_document_versions`
- 确定性证据门专用 Tool：`validate_evidence_refs`
- Streamable HTTP 生产形态和 stdio 本地形态
- 项目范围短期服务令牌、能力白名单、过期时间、issuer/audience 校验
- 本地 JSON 证据仓库及两个隔离项目的演示数据
- LangGraph `TenderEvidencePort` 的 MCP 客户端适配器
- MCP 协议、令牌、隔离、内容哈希、上下文读取留痕和适配器测试

## 核心安全边界

### 1. Tool 参数中没有 `case_id`

四个 Tool 和 Resource 都从服务令牌中取得：

- `case_id`
- `assessment_id`
- `agent_run_id`
- `subject`
- `allowed_tools`
- `issued_at` / `expires_at`
- `issuer` / `audience`

模型只能决定“查什么”，不能决定“查哪个项目”。这可以避免模型受文档提示注入影响后尝试越权读取其他项目。

### 2. 用户令牌不传给 MCP

MCP 使用专门签发、最长一小时、默认五分钟的内部服务令牌。当前原型用 HS256；未来接企业内部 OIDC/JWKS 时，只需要替换 `ScopedTokenCodec` / `ScopedTokenVerifier`，Tool 契约不变。

### 3. 检索命中不等于已读取证据

`search_tender_evidence` 返回的引用固定为 `context_read=false`。只有调用 `read_evidence_context` 后，服务才按 `(case_id, agent_run_id, evidence_id)` 写入本次运行的读取留痕。

`validate_evidence_refs` 会确定性检查：

- manifest 版本
- evidence / block / document 标识
- document 版本是否为当前 active
- 内容 SHA-256
- 当前 Agent run 是否真正读取过上下文

高风险结论仍由 LangGraph 的证据门决定是否通过，模型不能自行宣称“证据有效”。

### 4. 本阶段全部只读

Tool 都声明：

- `readOnlyHint=true`
- `destructiveHint=false`
- `idempotentHint=true`
- `openWorldHint=false`

服务不写报价、不批准立项、不修改项目资料，也不连接现有报价 RAG 集合。

## 当前代码结构

```text
AI_Middle_Office/
├── mcp_servers/tender_evidence/
│   ├── auth.py                 # 项目范围令牌与 MCP TokenVerifier
│   ├── contracts.py            # MCP 输入/输出和本地数据契约
│   ├── repository.py           # 可替换的证据仓库协议
│   ├── local_repository.py     # 本地 JSON 开发仓库
│   ├── service.py              # 鉴权、参数边界、读取留痕
│   ├── server.py               # FastMCP Resource 与 Tool
│   └── fixtures/demo_cases.json
├── app/agents/bid_intake/
│   └── mcp_adapter.py          # MCP -> TenderEvidencePort
├── scripts/
│   ├── tender_evidence_mcp_server.py
│   └── tender_evidence_issue_token.py
└── agent_tests/
    └── tender_evidence_mcp_checks.py
```

## 本地启动

以下命令使用 Phase 1 隔离依赖环境。开发密钥只用于本机，不应提交到 Git。

```powershell
$env:TENDER_MCP_JWT_SECRET = "<至少32字符的本地随机密钥>"
$env:TENDER_MCP_ISSUER = "http://127.0.0.1:8012"
$env:TENDER_MCP_AUDIENCE = "http://127.0.0.1:8012/mcp"

python AI_Middle_Office/scripts/tender_evidence_issue_token.py `
  --case-id CASE-DEMO-001 `
  --assessment-id ASSESSMENT-LOCAL-001 `
  --agent-run-id RUN-LOCAL-001

python AI_Middle_Office/scripts/tender_evidence_mcp_server.py `
  --transport streamable-http `
  --host 127.0.0.1 `
  --port 8012
```

把签发命令输出的令牌作为 MCP 客户端 Bearer Token。令牌只应保存在当前进程内，不写日志、不放 Tool 参数。

stdio 模式还需要把令牌放入当前子进程的 `TENDER_MCP_SCOPE_TOKEN`：

```powershell
python AI_Middle_Office/scripts/tender_evidence_mcp_server.py --transport stdio
```

## 当前数据后端的真实边界

本地仓库仅做：

- 加载并严格校验 JSON 数据契约
- 校验证据正文 SHA-256
- 确定性关键词检索
- 同一文档版本的相邻块读取
- 返回预先整理的版本冲突

它没有做：

- 上传、病毒扫描、OCR、表格解析
- MinIO 原文件读取
- MySQL 元数据持久化
- Milvus / BM25 混合检索
- 跨进程持久化审计

因此下一开发阶段应是“招标资料入库与真实证据仓库适配”，而不是先接真实大模型。

## 下一阶段建议

1. 建立招标项目、文档版本、解析块、证据读取审计的数据契约。
2. 接入现有文件存储边界，但为招标资料建立独立 collection/index，不复用报价成本 RAG。
3. 实现 MySQL + 对象存储 + 检索服务的 `TenderEvidenceRepository` 适配器。
4. 将 MCP 客户端改成 Worker 生命周期内的持久 async session，并把 LangGraph 执行切换到 `ainvoke`。
5. 在真实模型接入前完成恶意文档、跨项目访问、版本过期和检索降级测试。
