# QueryPilot 技术方案 v4

> 一个可验证、可解释、只读执行的 NL2AQL Agent

## 1. 项目目标

QueryPilot 将自然语言转换为受 Schema 约束的结构化查询计划，再由服务端编译器确定性生成 ArangoDB AQL，并在执行前完成语义检索、权限约束、静态检查和 `EXPLAIN` 验证。大模型不能直接生成或修改 AQL。

项目必须真实使用：

- **LangChain**：模型调用、Embedding、结构化输出。
- **LangGraph**：有状态工作流、条件分支、有限重试、Checkpoint。
- **Milvus Standalone**：通过 Docker 部署，保存语义目录与已审核示例的向量索引。

目标运行环境：腾讯云 4 vCPU、8 GiB RAM、60 GiB SSD、5 Mbps 带宽。大模型与 Embedding 走云 API，不在服务器运行本地模型。

### 1.1 用户故事

用户选择一个已注册的数据源，然后提问：

> 查询近 30 天购买过手机、但没有购买延保服务的用户，按消费金额降序排列，最多返回 50 人。

系统返回：

1. 对问题的结构化理解和必要假设。
2. 被使用的集合、字段、图关系和业务术语。
3. 最终 AQL 与 bind variables。
4. 查询结果、耗时和结果解释。
5. 可查看的 Agent 轨迹；失败时返回可操作的诊断。

### 1.2 MVP 范围

- 支持运行时接入多个 ArangoDB 数据源，以及文档查询、聚合和 1 至 3 跳图遍历。
- 自动扫描非系统集合、样例字段类型和 Named Graph 边定义，为每个数据源生成独立语义目录。
- 每个 `source_id` 使用独立数据库连接、Milvus alias 和查询白名单，禁止跨源回退执行。
- 单次问题与基于上一轮结果的追问。
- 查询只能读取白名单中的 Collection 和 Named Graph。
- 最多自动修复 2 次，单次总执行时间有硬上限。
- 默认最多返回 100 行，绝对上限 200 行。
- 提供查询历史、证据、AQL 和结果表格/图视图。
- 不做写操作 Agent、多 Agent或自动建表。
- 动态接入凭据第一阶段仅驻留内存；持久化必须使用云密钥管理或应用层加密。

## 2. 架构原则

### 2.1 先做模块深度，再做目录数量

系统只暴露少量稳定接口，把检索融合、AQL 安全策略和 LangGraph 节点细节隐藏在实现内。测试也通过这些接口验证行为，不直接测试内部节点顺序。

### 2.2 框架是实现细节

FastAPI 不进入领域类型，LangGraph State 不流入数据访问代码，LangChain `Document` 不出现在业务接口中，Milvus Row 不返回给 Agent。这样未来升级框架版本时，变化集中在一个模块内。

### 2.3 确定性逻辑优先于 LLM

- 权限、限制、集合白名单和查询执行由代码决定。
- LLM 负责理解问题、选择语义上下文和生成候选查询。
- LLM 不能批准自己的查询，也不能扩大自己的权限。

### 2.4 单体优先

第一版采用模块化单体：一个 FastAPI 进程承载 Agent 与管理接口，一个静态前端。Milvus、ArangoDB 和 PostgreSQL 是独立基础设施，不拆业务微服务，也不引入消息队列。

## 3. 系统上下文

```text
Browser
  |
  | HTTPS
  v
Nginx ---- static files ----> React Web
  |
  | /api
  v
FastAPI
  |
  +--> QueryAgent (LangGraph)
  |      +--> SemanticCatalog ----> Milvus ----> etcd + MinIO
  |      +--> PlanningModel ------> Cloud LLM API
  |      +--> SafeAqlExecutor ----> ArangoDB (read-only user)
  |      +--> RunRepository ------> PostgreSQL
  |
  +--> CatalogAdmin
         +--> semantic catalog manifest
         +--> Milvus index sync
```

只允许 Nginx 的 `80/443` 和运维使用的 `22` 暴露公网。其余端口只存在于 Docker 内部网络。

## 4. 核心模块与接口

### 4.1 QueryAgent

QueryAgent 是应用的主模块，内部实现使用 LangGraph。调用方不需要知道图中有多少节点。

```python
class QueryAgent(Protocol):
    async def stream(self, command: AskCommand) -> AsyncIterator[RunEvent]: ...
    async def resume(self, run_id: RunId) -> AsyncIterator[RunEvent]: ...
```

接口承诺：

- 每个 `run_id` 幂等；重复提交不会产生并行执行。
- 事件按序号递增，可安全重放。
- 成功时只返回经过 SafeAqlExecutor 执行的结果。
- 一个 Run 最多进行 2 次 AQL 修复。
- 任何错误都映射为稳定的业务错误码，不暴露数据库凭据或完整模型响应。

### 4.2 SemanticCatalog

SemanticCatalog 隐藏 Embedding、Milvus Schema、召回和融合策略。

```python
class SemanticCatalog:
    async def context_for(self, request: ContextRequest) -> ContextPack: ...
    async def publish(self, release: CatalogRelease) -> PublishReport: ...
```

`ContextPack` 只包含生成查询所需的最小上下文：相关集合、字段、边、术语、示例、目录版本和证据 ID。调用方不会看到 Milvus 的距离值或内部 Row。

### 4.3 SafeAqlExecutor

这是安全性最关键的深模块。生成、验证和执行之间使用不同类型，避免未经验证的字符串被误执行。

```python
class SafeAqlExecutor:
    async def prepare(
        self,
        candidate: AqlCandidate,
        policy: QueryPolicy,
    ) -> PreparedQuery: ...

    async def execute(self, query: PreparedQuery) -> QueryResult: ...
```

只有 `prepare` 能创建 `PreparedQuery`。其内部完成词法检查、资源白名单、bind variables 检查、结果外层限流包装和 ArangoDB `EXPLAIN`。执行接口不接受普通字符串。

### 4.4 RunRepository

RunRepository 保存产品需要的历史和审计，LangGraph Checkpoint 表仍由官方 PostgreSQL Checkpointer 管理。

```python
class RunRepository:
    async def create(self, run: NewRun) -> RunRecord: ...
    async def append(self, event: RunEvent) -> None: ...
    async def finish(self, outcome: RunOutcome) -> None: ...
    async def get(self, run_id: RunId) -> RunView | None: ...
```

### 4.5 PlanningModel seam

LLM 是真正的外部依赖，因此定义一个窄接口。生产使用 LangChain Adapter，测试使用确定性 Fake Adapter。

```python
class PlanningModel(Protocol):
    async def plan(self, prompt: PlanPrompt) -> StructuredQueryPlan: ...
    async def summarize(self, prompt: SummaryPrompt) -> AnswerSummary: ...
```

`AqlCompiler.compile(plan, schema, policy)` 是独立深模块。它负责字段和类型校验、边方向校验、bind variables、聚合粒度、输出列、资源声明与 AQL 渲染。计划无效时只允许模型重新规划；编译成功后模型不能接触 AQL。

不创建通用的 `invoke(messages)` 接口，因为它会把 Prompt、模型参数和结构化输出约束泄漏给所有调用方。

## 5. 依赖方向

```text
HTTP routes
    |
    v
application: QueryAgent, CatalogAdmin
    |
    v
domain: commands, plans, policies, outcomes, errors
    ^
    |
adapters: LangChain, Milvus, ArangoDB, PostgreSQL
```

依赖规则：

- `domain` 不导入 FastAPI、LangChain、LangGraph、pymilvus 或 python-arango。
- `application` 可以使用 LangGraph，但节点只能调用模块接口。
- `adapters` 将外部数据转换为领域类型。
- HTTP 路由只做认证、输入校验、调用和响应映射，不包含 Agent 逻辑。
- 不为每张表创建 Repository，也不为每个 LangGraph 节点创建公开类。

## 6. LangGraph 设计

### 6.1 状态图

```text
START
  |
  v
understand
  |
  +-- NEED_CLARIFICATION --> clarify --> END
  |
  v
retrieve_context
  |
  +-- INSUFFICIENT_CONTEXT --> clarify --> END
  |
  v
generate_candidate
  |
  v
prepare_query
  |
  +-- POLICY_REJECTED ---------------------> reject --> END
  |
  +-- INVALID / repair_count < 2 --> repair_candidate --+
  |                                                      |
  +<-----------------------------------------------------+
  |
  +-- INVALID / repair_count = 2 --> diagnostic --> END
  |
  v
execute_query
  |
  +-- REPAIRABLE / repair_count < 2 --> repair_candidate
  +-- TIMEOUT OR RESOURCE_LIMIT ---------> diagnostic --> END
  |
  v
summarize
  |
  v
complete
  |
  v
END
```

`POLICY_REJECTED` 永远不进入修复，因为不能让模型尝试绕过策略。只有语法、引用和可恢复的执行错误允许修复。

### 6.2 State

```python
class QueryState(TypedDict):
    run_id: str
    thread_id: str
    command: AskCommand
    catalog_version: str | None
    context: ContextPack | None
    plan: StructuredQueryPlan | None
    candidate: AqlCandidate | None
    prepared_query: PreparedQuery | None
    result: QueryResult | None
    failure: QueryFailure | None
    repair_count: int
```

State 不保存数据库连接、模型客户端或大量原始结果。大型结果只存摘要和受限预览。

### 6.3 Checkpoint 与流式事件

- 使用 LangGraph PostgreSQL Checkpointer，`thread_id` 对应会话，`run_id` 对应一次执行。
- HTTP 使用 `POST /api/v1/runs:stream` 返回 `text/event-stream`；前端用 `fetch` 读取流，不依赖只能 GET 的 EventSource。
- RunEvent 包含 `seq`、`type`、`occurred_at` 和经过脱敏的 payload。
- 断线后通过 `GET /api/v1/runs/{id}/events?after_seq=N` 补取事件。

## 7. LangChain 使用方式

LangChain 只位于两个 Adapter 内：

### 7.1 LangChainPlanningModel

- `ChatOpenAI` 或 OpenAI-compatible ChatModel 接入通义千问、腾讯混元或其他提供商。
- `with_structured_output(PydanticModel)` 只生成 `StructuredQueryPlan` 和 `AnswerSummary`。
- 每种调用有独立 Prompt、超时、温度和 Token 上限。
- 结构化规划温度设为 0 或供应商允许的最低值。
- 不使用开放式 ReAct Agent，不允许模型任意选择工具。

### 7.2 LangChainEmbeddingAdapter

- 通过 LangChain Embeddings 接口批量生成向量。
- `provider`、`model_name`、`dimension` 和 `revision` 写入 CatalogRelease。
- 模型或维度变化时创建新 Milvus Collection alias 指向的新版本，完成后原子切换，不能在原索引中混用向量。

## 8. 语义目录与 Milvus

### 8.1 自动目录与人工增强

ArangoDB 是弱 Schema 数据库。接入时通过有限采样自动发现字段和推断类型，并读取 Named Graph 的边方向；样例值不会写入语义目录。自动目录解决任意数据库的冷启动，但无法可靠推断币种、业务口径和指标含义。

仓库中的版本化 `catalog.yaml` 用于人工增强默认数据源，补充字段描述、别名、业务指标和审核后的 AQL 示例。动态数据源可以先使用自动目录，后续再叠加人工描述。

```yaml
version: commerce-v1
database: commerce
collections:
  orders:
    description: paid and cancelled orders
    fields:
      paid_amount:
        type: number
        description: actual paid amount in CNY
        aliases: [成交额, 实付金额, GMV]
graphs:
  commerce_graph:
    edges:
      placed:
        from: users
        to: orders
metrics:
  gmv:
    expression: SUM(order.paid_amount)
    filters: [order.status == "paid"]
```

### 8.2 Milvus 数据模型

Collection alias 前缀：`semantic_catalog_active`。每个数据源使用由 `source_id` 和哈希生成的独立 alias；旧的基础 alias 仅用于向后兼容。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | 稳定证据 ID |
| `release_id` | VARCHAR | 目录发布版本 |
| `source_id` | VARCHAR | 数据源 ID |
| `kind` | VARCHAR | collection、field、edge、metric、example |
| `entity` | VARCHAR | 对应实体名称 |
| `content` | VARCHAR | 可直接提供给模型的规范文本 |
| `embedding` | FLOAT_VECTOR | 稠密向量 |
| `approved` | BOOL | 示例是否人工审核 |
| `content_hash` | VARCHAR | 增量同步与去重 |

### 8.3 检索流程

1. 通过标识符和业务别名字典做确定性匹配。
2. 在 Milvus 中按 `source_id`、`release_id`、`approved` 过滤后执行向量召回。
3. 按 kind 配额合并：Schema/edge/metric 优先，示例不能挤掉 Schema。
4. 去重并执行 Token 预算裁剪。
5. 返回 `ContextPack` 和证据 ID。

MVP 不急于上 Reranker。先用离线 Recall@K 证明召回问题存在，再决定是否增加额外模型成本。

### 8.4 发布流程

`connect -> inspect -> infer schema -> embed -> write new collection -> smoke search -> switch source alias -> register runtime`

发布失败时 alias 不切换，线上继续使用旧版本。删除旧版本属于人工维护操作，不放进自动发布流程。

## 9. AQL 安全执行

### 9.1 候选结构

模型不能只返回裸字符串：

```python
class AqlCandidate(BaseModel):
    query: str
    bind_vars: dict[str, JsonValue]
    referenced_collections: set[str]
    referenced_graphs: set[str]
    intent: Literal["document", "aggregate", "traversal"]
```

### 9.2 `prepare` 的确定性流水线

1. 限制查询长度和 bind variable 数量。
2. 使用词法扫描而不是简单正则，拒绝写关键字与多语句。
3. 校验声明资源与实际 token 中资源一致，并与 `QueryPolicy` 白名单求交。
4. 禁止动态 Collection/Graph 名称；用户值必须进入 bind variables。
5. 校验图遍历方向和最大深度。
6. 用外层只读子查询包装结果限制，而不是脆弱地改写内部 AQL。
7. 使用只读账号调用 `EXPLAIN`，检查语法、集合与计划成本。
8. 生成不可由 HTTP/LLM 直接构造的 `PreparedQuery`。

说明：`EXPLAIN` 能验证语法、集合和执行计划，但 ArangoDB 文档字段可能不存在，因此它不能证明字段语义正确。字段正确性依赖语义目录、评测和执行结果。

### 9.3 执行限制

- ArangoDB 专用只读账号，只授权演示数据库和白名单集合。
- 单查询最大运行时间 8 秒；整个 Agent Run 最大 45 秒。
- 最大内存、结果行数、返回字节数和并发数均由服务端配置。
- 查询超时、资源超限、空结果与语法错误使用不同错误码。
- 审计日志记录 AQL 哈希、脱敏 AQL、证据 ID、目录版本、耗时和结果规模。

### 9.4 Prompt Injection 防护

- 语义目录内容视为不可信数据，只作为结构信息，不作为系统指令。
- XML/JSON 分区传递用户问题、目录证据和错误，不把它们拼进 system prompt 指令区。
- 检索文本中的“忽略规则”“执行写入”等内容不会获得指令权限。
- 最终安全由 SafeAqlExecutor 和数据库权限保证，而不是模型拒绝能力。

## 10. 数据存储

### 10.1 ArangoDB 演示模型

Vertex Collections：

- `users`
- `products`
- `orders`
- `categories`

Edge Collections：

- `placed`: users -> orders
- `contains`: orders -> products
- `belongs_to`: products -> categories
- `viewed`: users -> products

Named Graph：`commerce_graph`

数据生成必须使用固定随机种子，并包含可验证的边界案例：取消订单、退款、空值、重复购买、孤立顶点和跨月时间范围。

### 10.2 PostgreSQL

产品表：

- `data_sources`
- `catalog_releases`
- `runs`
- `run_events`
- `feedback`

Checkpoint 表由 `langgraph-checkpoint-postgres` 管理，不复制其状态到产品表。MVP 不引入 Redis；Nginx 完成基础限流，PostgreSQL 负责持久状态，减少服务器内存与运维负担。

## 11. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/sources` | 列出当前进程已注册的数据源 |
| POST | `/api/v1/sources` | 连接、扫描并注册 ArangoDB 数据源 |
| POST | `/api/v1/runs:stream` | 创建并流式执行查询 |
| GET | `/api/v1/runs/{run_id}` | 查询执行结果与摘要 |
| GET | `/api/v1/runs/{run_id}/events` | 断线后补取事件 |
| GET | `/api/v1/runs` | 查询历史 |
| POST | `/api/v1/runs/{run_id}/feedback` | 正确/错误反馈与备注 |
| POST | `/api/v1/catalog/releases` | 校验并发布语义目录 |
| GET | `/api/v1/catalog/active` | 当前目录版本 |
| GET | `/health/live` | 进程存活 |
| GET | `/health/ready` | PostgreSQL、Milvus、ArangoDB 就绪状态 |

公开演示使用访问码并在 Nginx 按 IP 限流，防止模型额度被恶意消耗。登录、RBAC 和多租户放到第二阶段。

## 12. 前端设计

主界面是查询工作台，不做营销首页：

- 左侧：会话和历史查询。
- 中间：自然语言输入、假设确认、结果表格或图视图。
- 右侧：Agent 轨迹、证据、AQL、bind variables、执行指标。
- 图结果使用 Cytoscape.js；普通结果使用虚拟化表格。
- AQL 默认可见但只读，避免产品看起来像隐藏 SQL/AQL 的黑盒。
- 错误状态显示错误类别和下一步，不展示内部堆栈。

## 13. 仓库结构

```text
querypilot/
├── apps/
│   ├── api/
│   │   ├── src/querypilot/
│   │   │   ├── domain/          # 纯类型、策略、错误
│   │   │   ├── application/     # QueryAgent、CatalogAdmin
│   │   │   ├── adapters/        # LangChain、Milvus、ArangoDB、Postgres
│   │   │   ├── transport/       # FastAPI routes 与 DTO 映射
│   │   │   └── bootstrap.py     # 依赖组装
│   │   └── tests/
│   └── web/
├── catalog/
│   ├── commerce/catalog.yaml
│   └── examples.yaml
├── datasets/
│   ├── seed/
│   └── eval/
├── infra/
│   ├── compose.yaml
│   ├── compose.prod.yaml
│   ├── nginx/
│   └── scripts/
├── docs/
│   ├── decisions/
│   ├── security-model.md
│   └── evaluation.md
├── .github/workflows/
├── .env.example
├── Makefile
└── README.md
```

## 14. Docker 部署

### 14.1 容器

```text
nginx
api
postgres
arangodb
milvus-standalone
etcd
minio
```

前端构建产物由 Nginx 托管，不常驻 Node 容器。Catalog 发布作为 API 管理命令或一次性 Compose Job 运行，不常驻 Worker。

镜像全部固定到经过测试的明确版本或 digest，不使用 `latest`。版本升级通过独立 PR 和集成测试完成。

### 14.2 8 GiB 内存预算

| 进程 | 目标上限 |
|---|---:|
| Milvus Standalone | 2.4 GiB |
| MinIO | 0.45 GiB |
| etcd | 0.30 GiB |
| ArangoDB | 1.35 GiB |
| PostgreSQL | 0.45 GiB |
| FastAPI | 0.80 GiB |
| Nginx | 0.10 GiB |
| Docker + OS + 文件缓存余量 | 2.15 GiB |

配置 4 GiB swap 仅用于峰值保护。若日常 swap 持续增长，应减少 Milvus/ArangoDB 缓存或升级内存，而不是扩大 swap。

### 14.3 磁盘和备份

- 系统与镜像预留 20 GiB。
- 数据卷和备份软预算 30 GiB。
- 保留至少 10 GiB 安全余量。
- Docker 日志：`max-size=10m`、`max-file=3`。
- PostgreSQL 与 ArangoDB 每日备份，服务器保留 7 天。
- Milvus 索引由 `catalog.yaml` 可重建，备份 Manifest 与 MinIO 数据但优先保证业务库备份。
- 磁盘达到 70% 告警，80% 停止 Catalog 发布和数据导入。

## 15. 测试策略

### 15.1 模块接口测试

- QueryAgent：使用 Fake PlanningModel、测试目录和测试数据库，从输入验证到最终 Outcome。
- SemanticCatalog：真实启动 Milvus 测试实例，验证发布、过滤、版本切换和 Recall@K。
- SafeAqlExecutor：真实启动 ArangoDB，验证 prepare/execute、只读权限、资源限制和错误分类。
- RunRepository：真实 PostgreSQL，验证事件顺序、幂等和恢复。

不要 Mock Milvus SDK 或 ArangoDB Driver 的每个方法；那只能证明 Mock 与实现一致。对外部云模型使用 Fake Adapter，避免测试产生费用和随机性。

### 15.2 评测集

第一版提交 100 条固定评测：

- 35 条过滤与排序。
- 25 条聚合。
- 25 条图遍历。
- 15 条歧义、越权或危险请求。

核心指标：

- Execution Accuracy。
- Valid-at-1：第一次候选通过 prepare 的比例。
- Retrieval Recall@K。
- Policy Rejection Precision/Recall。
- 平均修复次数。
- P50/P95 延迟、Token 与单次成本。

AQL 字符串完全匹配只用于调试，不作为正确性主指标；不同查询可以产生等价结果。

### 15.3 CI

- Pull Request：lint、type check、单元测试、安全规则测试。
- Main：启动测试 Compose，运行模块集成测试和 20 条冒烟评测。
- Nightly/手动：运行完整 100 条云模型评测并生成报告，避免每个 PR 消耗模型费用。

## 16. 纵向实施计划

### Milestone 1：最短正确闭环

- Compose 启动 ArangoDB、Milvus、PostgreSQL。
- 建立最小电商数据与 10 条金标准问题。
- 实现 SemanticCatalog 发布与检索。
- 实现一个 LangGraph：检索 -> 结构化规划 -> 编译 -> prepare -> 执行 -> 返回。
- 用命令行完成第一条真实 NL2AQL 查询。

验收：三个必选技术都在真实路径上运行，而不是空壳依赖。

### Milestone 2：安全与纠错

- 实现完整 SafeAqlExecutor、只读账号与资源限制。
- 增加 StructuredQueryPlan、AqlCompiler 和有限次计划修复。
- 覆盖危险关键字、越权资源、深遍历、超限和 Prompt Injection 测试。
- 接入 PostgreSQL Checkpointer 与 RunRepository。

验收：所有已知写入测试在模型之外被确定性拒绝。

### Milestone 3：产品界面

- FastAPI 流式接口与 React 工作台。
- 轨迹、证据、AQL、表格与图结果视图。
- 历史、反馈、断线事件补取和访问码。

验收：新用户无需终端即可完成查询并理解系统如何得出结果。

### Milestone 4：评测与上线

- 扩展到 100 条评测并输出基线报告。
- 配置 Nginx、TLS、日志轮转、备份和资源告警。
- GitHub Actions、架构图、演示 GIF、在线地址和安全说明。

验收：一条命令可启动本地环境，线上演示可重复，README 给出真实评测数据。

## 17. 验收标准

- LangChain、LangGraph、Milvus 均处于用户查询的真实执行路径。
- `docker compose up -d` 后所有健康检查通过。
- 已知写操作和越权查询拒绝率 100%。
- Agent 不存在无界循环，修复上限为 2。
- 100 条评测 Execution Accuracy 目标不低于 80%。
- Valid-at-1 目标不低于 85%。
- Retrieval Recall@5 目标不低于 95%。
- 服务重启后已持久化 Run 可查询，LangGraph Checkpoint 可恢复。
- 服务器稳定运行时无持续 swap，磁盘使用率低于 70%。
- GitHub 不包含密钥、数据卷、用户原始查询结果或生产备份。

## 18. 明确不做的设计

- 不为展示“Agent 感”而拆成多个角色 Agent；当前问题是受控工作流，不需要协商系统。
- 不让 LLM 通过通用 Tool Calling 直接执行任意 AQL。
- 不依赖 Prompt 声明“只读”作为安全措施。
- 不在 8 GiB 主机部署本地大模型、Elasticsearch、Grafana 或 Kubernetes。
- 不同时支持 SQL、Cypher 和 AQL；先把 NL2AQL 的评测和安全做扎实。
- 不提前抽象多个向量库或图数据库；出现第二个真实 Adapter 后再建立新的 seam。
