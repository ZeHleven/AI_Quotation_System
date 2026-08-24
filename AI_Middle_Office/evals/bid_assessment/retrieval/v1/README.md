# RQ2 跨项目检索评测私有目录

本目录只保存 RQ2 总收口的合同说明和不含真实资料的模板。真实 Gold/Holdout 问题、Target、PDF 路径、报告和 Execution Ledger 必须保存在被 `.gitignore` 排除的私有目录，不提交 Git。

## 最小组合

- Development Gold：3 个项目、每项目 20 题，共 60 题；
- Holdout：2 个从未参与 RQ1/RQ2 的项目、每项目 20 题，共 40 题；
- 每项目严格一份主招标 PDF；
- 每题由一人标注、另一人独立复核；
- “香港中心”只能进入 Development，不能进入 Holdout。

## 顺序

1. 使用 `dataset-template.example.json` 分别建立私有 Development/Holdout 文件；
2. 由 `with_dataset_hash()` 计算内容 Hash，不能手工写占位 Hash；
3. 用 `validate-dataset` 和 `validate-isolation` 检查规模、双人复核和零重叠；
4. 用 `artifact-snapshot.example.json` 建立不可变 Development Snapshot；
5. 用 `materialize-project` 为每个项目投影冻结的 PDF-C3 cases；
6. 获得用户明确授权后，在完全隔离的本地 Worker 运行 RQ2-B/RQ2-C Development A/B 并聚合；
7. Development 全部门通过后，用其 Snapshot/报告和 sealed Holdout 建立 Pre-Holdout Freeze；
8. 再次确认正式 Holdout 授权，以 `begin-holdout` 排他写入 Ledger；
9. 只执行一次 Holdout baseline→candidate，最后用 `aggregate` 收敛报告与 Ledger。

这些命令属于报价资料研判 Agent 测试/真实样例/模型评测；没有用户明确授权时不得执行。

## 保密与防泄漏

Holdout 问题、Target 和报告不得出现在公开文档、Prompt、Query Optimizer、字段别名、BM25F 权重或 Reranker 参数中。Holdout 失败后不能修改本 Freeze 再运行；下一轮必须新增 Development 项目并建立新的 Holdout 版本。
