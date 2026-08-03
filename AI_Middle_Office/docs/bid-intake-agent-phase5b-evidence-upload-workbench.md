# 报价资料研判 Agent Phase 5b：前端资料入库闭环

## 本阶段结论

Agent 工作台已经补齐以下用户闭环：

```text
选择投标项目
  -> 系统自动识别资料类型
  -> 上传一份或多份招标资料
  -> 后台异步解析
  -> 生成不可变 evidence document / blocks
  -> 自动生成新的 active manifest
  -> 自动刷新 Agent readiness
  -> 解除资料证据阻断
  -> 发起研判
```

本阶段复用 Phase 3a 已完成的原件保存与解析任务、Phase 2 证据
manifest、Phase 3b 索引降级策略和 Phase 5a Agent Runtime，不新增数据库
结构，不改变研判、总经办标准或报价规则。

## 页面能力

入口仍为 `/admin/bid-intake-agent`。

工作台新增“上传并解析招标资料”区域：

- 支持 `.pdf`、`.docx`、`.xlsx`、`.xlsm`、`.txt`、`.md`；
- 单批最多选择 10 份资料；
- Phase 5c 后由系统自动识别招标文件、澄清、补遗、图纸、工程量清单和其他资料，
  用户不再手工选择类型；
- 同文件重复提交复用幂等任务，不重复保存原件；
- 展示最近解析任务、文件类型、大小、状态、当前环节、结果和时间；
- 展示排队、解析、证据入库、完成、等待重试和失败状态；
- 可恢复的任务支持人工重试；
- 存在运行中任务时每 2.5 秒轻量刷新解析进度；
- 解析完成后自动刷新 readiness 和证据清单版本；
- 资料未就绪时继续禁用“发起研判”，不会绕过证据门。

## 复用接口

- `POST /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs`
- `GET /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs`
- `GET /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs/{job_uuid}`
- `POST /api/v1/admin/bidding/projects/{project_uuid}/evidence/parse-jobs/{job_uuid}/retry`
- `GET /api/v1/admin/bidding/projects/{project_uuid}/bid-intake/readiness`

后端成功解析时负责创建证据文档、证据块和新的 active manifest；混合索引
尚未部署或尚未完成时，Agent 继续使用数据库词法检索兜底，不阻断研判。

## 验证

- Vite production build：通过，`1631 modules transformed`；
- 前端上传闭环契约、证据解析管线、证据存储、Agent Runtime 和 SPA
  路由聚焦回归：`25 passed`；
- 当前运行态 `/health/ready=ready`，数据库正常，Celery Worker 2 个在线；
- `/admin/bid-intake-agent` 已确认引用本次最新构建资源；
- `git diff --check`：通过。

非阻断提示：

- Vite 仍有既有大 chunk 提示；
- 当前 Python 环境仍有既有 RequestsDependencyWarning 和 pytest cache
  权限提示。

## 人工验收

1. 打开左侧“报价资料研判 Agent”。
2. 选择一个尚未有证据的投标项目。
3. 在“上传并解析招标资料”直接上传文件，资料类型由系统自动识别。
4. 确认任务依次显示“排队中 / 解析中 / 已完成”。
5. 确认顶部“证据清单”出现版本号，“可用资料”数量增加。
6. 确认“缺少有效证据清单 / 没有解析完成的招标资料”提示消失。
7. 点击“发起研判”，继续验收 Agent 结果与人工决策。

扫描版 PDF 如果无法提取文字，应先 OCR 后重新上传；旧版 `.doc` 和 `.xls`
应另存为新版格式。
