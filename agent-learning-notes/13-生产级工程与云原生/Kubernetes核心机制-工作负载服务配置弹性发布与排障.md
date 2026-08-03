---
title: Kubernetes 核心机制：工作负载、服务、配置、弹性、发布与排障
category: 生产级工程与云原生
tags:
  - Kubernetes
  - K8s
  - Deployment
  - Service
  - HPA
  - 云原生
sources:
  - https://kubernetes.io/docs/concepts/
  - https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
  - https://kubernetes.io/docs/concepts/workloads/pods/probes/
reviewed_at: 2026-07-30
status: 已整理
---

# Kubernetes 核心机制：工作负载、服务、配置、弹性、发布与排障

## 核心结论

Kubernetes 的核心不是“运行 Docker”，而是：

> 用户声明期望状态，控制器持续比较实际状态并进行调谐。

```text
声明：我要 3 个健康副本
       ↓
API Server 保存期望状态
       ↓
控制器发现实际只有 2 个
       ↓
调度器选择节点，节点代理启动新 Pod
       ↓
实际状态逐步接近期望状态
```

它解决的是容器化应用的集群调度、自愈、服务发现、弹性和发布治理，但也引入控制面、网络、存储、权限和运维复杂度。

---

## 1. 从容器到 Kubernetes 对象

### Pod

Pod 是最小调度单位，可包含一个或多个紧密协作的容器。它们共享网络命名空间和可声明的卷。

要点：

- Pod 不是长期稳定的“服务器”；
- 被替换后 IP 可能变化；
- 应通过控制器管理副本，通过 Service 提供稳定访问；
- 业务状态尽量外置到数据库、对象存储或持久卷。

### Deployment 与 ReplicaSet

Deployment 适合无状态应用，负责：

- 声明副本数；
- 滚动更新；
- 暂停与继续发布；
- 版本历史和回滚；
- 通过 ReplicaSet 维持实际副本。

修改镜像后，Kubernetes 会逐步创建新 Pod、缩减旧 Pod。是否真正无损，还取决于就绪检查、优雅停机、连接排空和版本兼容。

### StatefulSet、DaemonSet、Job、CronJob

| 对象 | 适合场景 |
|---|---|
| StatefulSet | 需要稳定身份、顺序或持久存储的有状态服务 |
| DaemonSet | 每个或指定节点运行一个代理，如日志采集 |
| Job | 执行到完成的一次性任务 |
| CronJob | 按计划创建 Job |

异步报价任务通常由业务队列管理，不应仅因为“异步”就改成 Job；是否使用 Job 取决于任务隔离、调度和生命周期需求。

---

## 2. Service 与入口流量

Pod IP 会变化，Service 用标签选择后端 Pod，并提供稳定的服务发现和访问入口。

常见类型：

| 类型 | 用途 |
|---|---|
| ClusterIP | 仅集群内部访问 |
| NodePort | 通过每个节点端口暴露 |
| LoadBalancer | 通过云或基础设施负载均衡器暴露 |

外部 HTTP 流量通常再经过 Ingress Controller 或 Gateway API，负责域名、TLS 和路由。

关键理解：

- Service 只会把流量发给就绪端点；
- “Pod 在运行”不等于“Pod 已准备接流量”；
- NetworkPolicy 用于限制允许的网络通信，但需要网络插件支持。

---

## 3. 配置、密钥与存储

### ConfigMap 与 Secret

- ConfigMap：非敏感配置；
- Secret：密码、Token、证书等敏感数据的抽象。

Secret 不等于天然安全。仍要配置静态加密、RBAC、审计、轮换和最小暴露，避免把 Secret 明文提交到 Git。

配置变更策略：

- 配置与镜像版本可追溯；
- 变更前验证；
- 通过滚动重启或配置热更新生效；
- 关键配置可快速回退。

### PV、PVC 与 StorageClass

```text
Pod 申请 PVC
→ PVC 绑定 PV
→ StorageClass 可负责动态创建存储
```

数据库上 Kubernetes 不是“自动安全”。仍需：

- 存储可靠性；
- 数据备份和恢复演练；
- 主从或集群机制；
- 故障域规划；
- 升级和迁移策略。

---

## 4. 健康检查与优雅终止

| Probe | 回答的问题 | 失败后的行为 |
|---|---|---|
| Startup | 慢启动是否已完成 | 完成前保护其他探针 |
| Liveness | 进程是否卡死、需要重启 | 重启容器 |
| Readiness | 当前是否可以接流量 | 从 Service 端点移除 |

常见错误：

- Liveness 检查了外部数据库，数据库抖动导致所有应用被重启；
- 探针阈值过紧，启动稍慢便形成重启循环；
- Readiness 只返回固定 `200`，不能反映关键依赖；
- 终止宽限期过短，在途任务被强制中断。

异步 Worker 的健康判断还要考虑：能否从队列取任务、心跳是否更新、任务是否长期卡住，而不是只看进程存在。

---

## 5. 资源、调度与弹性

### Requests 与 Limits

- `requests`：调度时保证和计算容量的依据；
- `limits`：容器可使用资源的上限。

典型后果：

- CPU 超限通常被节流；
- 内存超限可能触发 OOM；
- requests 过低会过度装箱，节点高峰互相争抢；
- requests 过高则浪费容量或导致 Pod 无法调度。

### 调度控制

需要理解：

- label 与 selector；
- nodeSelector / affinity；
- taint 与 toleration；
- topology spread；
- PodDisruptionBudget。

这些机制分别解决放到哪里、哪些节点拒绝普通负载、如何跨故障域分散、维护时至少保留多少可用副本。

### HPA

Horizontal Pod Autoscaler 根据 CPU、内存或自定义指标调整副本数。

AI 应用常见陷阱：

- 只按 CPU 扩容，忽略队列积压和外部模型并发上限；
- 新副本启动慢，扩容已来不及；
- 扩容应用却没有扩容数据库连接或第三方配额；
- 消费者扩容造成下游重试风暴。

更合适的弹性信号可能是：

```text
队列待处理数
最老任务等待时间
活跃任务数
模型并发占用
请求速率与延迟
```

---

## 6. 发布、灰度与回滚

### 滚动更新

Deployment 通过 `maxSurge` 和 `maxUnavailable` 控制新旧副本替换速度。

无损发布的条件：

1. Readiness 真实可靠；
2. 新旧版本 API 和数据兼容；
3. 优雅停机与连接排空；
4. 观测窗口内指标正常；
5. 可快速停止或回滚。

### 灰度与蓝绿

- 滚动发布：逐步替换副本；
- 灰度/金丝雀：先给小比例流量或特定用户；
- 蓝绿：同时保留两套环境，切换入口。

Kubernetes 原生 Deployment 能管理滚动更新，但精细的百分比流量、自动指标判定通常还需要网关、服务网格或渐进式交付控制器。

### 数据库迁移

代码回滚不代表数据库能回滚。生产迁移应采用 expand-contract：

```text
先新增兼容结构
→ 部署兼容新旧结构的代码
→ 回填与验证
→ 切换读写
→ 最后删除旧结构
```

破坏性迁移、消息格式和缓存结构必须单独设计兼容窗口。

---

## 7. Kubernetes 排障框架

先看对象状态与事件，再进容器，不要一上来删除 Pod。

```bash
kubectl get pods -n <namespace> -o wide
kubectl describe pod <pod> -n <namespace>
kubectl logs <pod> -n <namespace> --previous
kubectl get events -n <namespace> --sort-by=.lastTimestamp
kubectl get deploy,rs,svc,endpointslice -n <namespace>
kubectl top pod -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>
kubectl rollout history deployment/<name> -n <namespace>
```

### 常见状态

| 现象 | 优先检查 |
|---|---|
| Pending | 资源不足、PVC、调度约束、污点 |
| ImagePullBackOff | 镜像名、凭据、网络、仓库权限 |
| CrashLoopBackOff | 当前和上一次日志、启动命令、配置、探针 |
| Running 但无流量 | Readiness、Service selector、EndpointSlice、端口 |
| 延迟突然升高 | 资源节流、下游、连接池、重试、节点异常 |
| 发布后失败 | 新旧版本差异、配置、迁移、镜像、探针 |

标准思路：

```text
Deployment → ReplicaSet → Pod → Container
Service → EndpointSlice → Pod
Config/Secret → 环境变量或挂载
PVC → PV → 存储后端
事件 → 日志 → 指标 → Trace
```

---

## 8. 报价中台的迁移映射

### 当前事实

报价中台目前使用：

- Windows 上的 FastAPI 和前端；
- CentOS 单机 Docker Compose 上的 RAG、Milvus、Redis、MinIO 等服务；
- 尚未部署 Kubernetes。

因此，以下仅是扩展场景设计，不是已完成能力。

### 需求成立时的可能映射

| 当前组件 | Kubernetes 对象建议 | 关键注意点 |
|---|---|---|
| FastAPI 网关 | Deployment + Service | Readiness、连接排空、无状态化 |
| Celery Worker | Deployment | 按队列积压扩容、任务幂等 |
| RAG API | Deployment + Service | 模型缓存、启动探针、内存 requests |
| 定时治理任务 | CronJob | 并发策略、失败重试、历史清理 |
| Redis / Milvus / MySQL | 优先托管服务或成熟运维方案 | 数据高可用、备份恢复、升级 |
| 配置 | ConfigMap | 环境分离、版本追踪 |
| 密钥 | Secret + 外部密钥系统 | 加密、RBAC、轮换 |
| 外部入口 | Ingress/Gateway | TLS、鉴权、限流 |

### 什么时候值得迁移

- 单机已成为明确的可用性瓶颈；
- 服务需要独立扩缩容；
- 多环境和发布频率显著增加；
- 团队具备集群运维、监控和安全能力；
- 迁移收益大于平台复杂度。

---

## 9. 面试回答模板

### Kubernetes 如何实现自愈？

> 用户声明期望状态，控制器持续调谐。Deployment 通过 ReplicaSet 维持副本；Pod 或节点异常后重新调度；Liveness 可触发容器重启；Readiness 决定是否接流量。但自愈只覆盖已建模的故障，不会自动修复数据损坏、错误配置或业务逻辑问题。

### Liveness 和 Readiness 有什么区别？

> Liveness 判断容器是否需要重启，Readiness 判断当前是否可以接收流量。把外部依赖直接放进 Liveness 容易在依赖抖动时造成集体重启，通常应更谨慎地把依赖状态用于 Readiness 或降级判断。

### 如何设计 AI Worker 的扩容？

> 我不会只看 CPU，会综合队列长度、最老任务等待时间、活跃任务数和外部模型并发配额。扩容前要保证任务幂等、租约和重试边界，并检查数据库连接池与下游限额，否则扩容会把压力放大到依赖。

### 你在项目中用过 Kubernetes 吗？

> 当前报价中台在 CentOS 单机 Docker Compose 上运行，我没有把 Kubernetes 描述成已经落地。现有规模下先把健康检查、日志、备份和 CI 做扎实更划算。我已经能给出迁移对象映射、探针、资源、弹性和发布方案，并会在多节点高可用和独立扩缩容需求成立时推进迁移。

---

## 10. 复习清单

- [ ] 能解释期望状态与控制器调谐
- [ ] 能区分 Pod、Deployment、StatefulSet、Job 和 CronJob
- [ ] 能解释 Service、Ingress/Gateway 和 NetworkPolicy
- [ ] 能区分三类 Probe
- [ ] 能解释 requests、limits、HPA 与队列弹性
- [ ] 能说明滚动、灰度、蓝绿与数据库迁移的边界
- [ ] 能用对象链路排查 Pending、CrashLoop 和无流量问题
- [ ] 能诚实说明报价中台尚未使用 Kubernetes

## 延伸阅读

- [Linux 与 Docker 生产运维](./Linux与Docker生产运维-进程网络存储安全与故障排查.md)
- [Git、测试工程与 CI/CD](./Git测试工程与CI-CD-分支质量门制品发布与回滚.md)
- [云原生可观测性](./云原生可观测性-PrometheusGrafanaOpenTelemetry与SLO.md)
