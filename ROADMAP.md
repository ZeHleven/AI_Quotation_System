# 系统升级路线图

> 最后更新：2026-04-21

---

## 高优先级

### ~~1. N8N conversationId 硬编码~~（✅ 2026-04-21 已完成）
- **位置**：`AI_Middle_Office/app/api/v1/chat.py`
- **修复**：改为每次请求生成 `str(uuid.uuid4())`，彻底隔离多用户上下文

### ~~2. N8N Webhook 无签名验证~~（✅ 2026-04-21 已完成）
- **修复**：FastAPI 每次请求计算 `HMAC-SHA256(WEBHOOK_SECRET, JSON.stringify(body))` 并附加 `X-Webhook-Signature` 头
- **N8N 侧**：两个 workflow（budget-calc / budget-push）Webhook 节点后新增 Code 节点验签，签名错误直接抛出异常终止流程
- **密钥存储**：`AI_Middle_Office/.env` 的 `WEBHOOK_SECRET` 字段

### ~~3. 用户配额无管理界面~~（✅ 2026-04-21 已完成）
- **后端**：`chat.py` 新增 `GET /admin/users` 和 `PATCH /admin/users/{id}/quota` 两个接口
- **前端**：`admin.html` 顶部新增员工账号 & 额度管理面板，支持直接在表格内输入新额度并一键确认

---

## 中优先级

### ~~4. 报价历史记录~~（✅ 2026-04-21 已完成）
- **后端**：新增 `QuoteHistory` 表（id/username/created_at/total_amount/item_count/payload_json），confirm_push 成功后自动写入
- **接口**：`GET /api/v1/history`，普通用户查自己，admin 可按 username 筛选全员
- **前端**：`index.html` 输入区新增"历史记录"按钮，点击弹出右侧抽屉展示历史列表，支持翻页和查看明细

### 5. RAG 检索效果评估（待完成）
- **问题**：不知道混合检索的实际命中率
- **方案**：引入 RAGAS 或人工标注集，定期跑指标

### 6. sync_milvus 每次重载 embedding 模型（待完成）
- **位置**：`chat.py:355` `SentenceTransformer(...)`
- **问题**：每次同步都在 Windows 端加载 768 维模型，耗时且占内存
- **方案**：让 CentOS RAG 服务暴露 `/admin/reload` 接口，由它完成向量化

---

## 低优先级

### 7. 前端无重试机制（待完成）
- **问题**：N8N 超时或 GLM-4V 失败时只显示红字，无重试按钮
- **方案**：前端加重试逻辑和友好的错误提示

### 8. Docker 日志无集中收集（待完成）
- **问题**：CentOS 四个容器日志分散，排查需逐个 `docker logs`
- **方案**：加 Loki 或简单文件日志聚合

### 9. admin 初始密码过于简单（待完成）
- **问题**：默认密码 `123`，存在安全风险
- **方案**：强制首次登录修改密码

---

## 已完成

- [x] RAG 微服务迁移 CentOS（Docker 容器化）
- [x] Milvus 蓝绿零停机切换
- [x] GLM-4V 静默假数据修复（改为返回 None）
- [x] N8N SQL 注入修复 + ZHIPU_API_KEY 环境变量化
- [x] BM25 升级真混合检索（jieba 分词 + RRF 融合 + 低分过滤）
- [x] 知识库数据扩充至 40 条（北京 2024 年市场行情）
- [x] 前端统一入口 + FastAPI 托管三个 HTML 页面
- [x] 真实 JWT 鉴权替换 Mock 登录
- [x] Windows 任务计划程序自启服务
