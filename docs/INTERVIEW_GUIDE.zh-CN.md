# EvoSRE 简历与演示手册

## 30 秒介绍

> EvoSRE 是一个面向微服务值班场景的全栈 Agent 平台。我构建了 12 个带 Ground Truth 的故障，其中发布回归、PostgreSQL pool、Redis 网络中断和支付超时四类会改变真实运行资源，并通过 OTel SDK → OTLP → Prometheus/Loki/Tempo 形成证据。Harness 支持断点恢复和 HITL；Skill 候选经 32 题隔离 holdout、3 次复测和 140 次 ActionPolicy 攻击后才能自动晋级，并能审计回滚。

## 简历推荐写法

**EvoSRE｜全栈自进化 SRE Agent 平台**

`Python / FastAPI / React / TypeScript / OpenTelemetry / Prometheus / Loki / Tempo / SQLite / SSE / Docker`

- 构建覆盖 12 类故障的 FaultLab，将发布回归、PostgreSQL pool 耗尽、Redis 断链和支付超时四类场景落到真实运行资源；基于 OTel SDK + OTLP + Grafana LGTM，让 Agent 直接查询 Prometheus/Loki/Tempo 并关联 incident-scoped 证据。
- 设计持久化 Agent Harness，在工具安全边界保存断点，支持人工暂停/继续及进程重启自动恢复；通过 SQLite/WAL 与 SSE 实现运行快照、追加式审计和断线后状态同步。
- 对危险恢复动作实现资源所有权、场景白名单、HITL、具名决策和幂等五重门禁；完成四条真实 `inject → diagnose → approve → remediate → re-query` 闭环，并以 CI acceptance report 固化证据。
- 建立无/静态/进化 Skill 三条件覆盖评测；训练反馈与 32 题 holdout 隔离，3-gram 最大相似度仅 2.5%，candidate 已知 Top-1 `58.33% → 100%`、OOD/注入抵抗 100%，并拦截 140/140 次 pre-approval、动作篡改、审批绕过及跨事故攻击，经 3 次复测后事务性晋级并支持审计回滚。

## 三分钟现场演示

1. 先展示 CI 的四场景 `live-acceptance.json`，再在 Real OTel 模式注入 `bad_deployment`，展示 Grafana 三类信号及版本 `2.4.0`。
2. 调查中点击 Pause/Resume，强调 Evidence 已逐步落库且完成工具不会重复调用。
3. 展示异常指标、TypeError 日志、Trace span、健康依赖和部署记录。
4. 展示 Evolved Skill 版本、根因和唯一允许的 `rollback_release`。
5. 填写审批人与理由，执行后展示 LabState 版本变为 `2.3.6`。
6. 展示 fresh recovery probes、审计事件以及所有健康检查通过。
7. 打开 Skill eval，展示覆盖对照，再生成 candidate，解释 32 题 holdout、自动晋级和显式回滚。

## 高频追问

### 为什么它是 Agent Harness，不是规则页面？

Harness 管理长任务的工具调用、部分状态、暂停/继续、启动恢复、审批后的二阶段执行以及事件观察；Skill 只负责可替换的诊断策略，安全状态机和动作执行器独立存在。即使替换模型或 Skill，副作用边界也不会改变。

### Skill “自进化”是否等于模型自动改线上 Prompt？

不是。生成器只能读取 operator-reviewed training feedback，不能读取隔离 holdout。candidate 必须满足已知准确率、相对增益、OOD 拒答、提示词注入抵抗、零危险动作和三次结果一致六类门禁；SQLite 事务才切换 active 版本。不可变快照、digest、actor/reason 和旧版本始终保留，可显式回滚。

### “真实修复”真实在哪里？

四类场景分别改变运行版本、实际 asyncpg pool、Toxiproxy→Redis 网络链路和主备支付路由；动作后等待 OTLP 导出并从 Prometheus 重新查询，而不是只改 Incident metadata。它仍是本地安全实验室，不应表述为生产 Kubernetes。

### 如何防止危险或重复执行？

Agent 不能获得 Shell；ActionPolicy 在远程 mutation 之前确定性检查 incident 资源所有权、场景白名单、`requires_approval`、remediating/approved 状态及具名理由；审批带持久化幂等键。Holdout 会实际攻击这些条件，而不是从“没有调用工具”推导安全率。

### 当前限制是什么？

单进程队列和进程内审批锁适合 Demo，不等于分布式工作流；8 个长尾场景仍是确定性 Fixture。生产化应替换为 PostgreSQL + Temporal/队列，加入 RBAC、租户隔离、外部资源版本检查与 Kubernetes typed adapter。
