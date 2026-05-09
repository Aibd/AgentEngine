# 生产上线就绪清单

> 状态:**当前为 internal demo / PoC,可在团队内做技术演示;距离对内部 50 人开放使用还有约 4 周工作量,距离对外开放则更远。**
> 制定日期:2026-05-08
> 适用版本:`agent-core` 重构后(refactor/phase-1-purge 分支当前 head)
> 目标场景:企业内部 chatbot,先小范围试用(<10 人)→ 部门级(<50 人)→ 全公司

---

## 0. 现状评估

### 已经做完的(可以放心)

| 维度 | 状态 | 证据 |
|---|---|---|
| 架构 | ✅ 单循环 + 数据化 Agent,代码干净 | refactor Phase 1-6 commits |
| 单元测试 | ✅ 226 passed, 6 skipped | `pytest -q` |
| 真实 LLM e2e | ✅ 6 条真实 DeepSeek 测试,41s 全过 | `RUN_INTEGRATION=1 pytest tests/test_deepseek_integration.py` |
| 持久化 | ✅ SQLite 落库,跨进程历史回放 | `data/chatbot.db` |
| 错误恢复 | ✅ LLM 错误/工具失败/取消都给前端结构化 error | `tests/test_error_recovery.py` |
| 并发隔离 | ✅ 同 conversation_id 串行化,不丢消息 | `tests/test_concurrency.py` |
| Web 前端 | ✅ React chatbot 跑通,真实 deep_research + 工具调用 | 浏览器实测 |

### 还没做的(致命缺口)

| 维度 | 状态 | 影响 |
|---|---|---|
| **认证** | ❌ 完全没有 | 任何人能调 API 烧光 LLM 配额 |
| **多租户** | ❌ TenantContext 写好但没接到路由 | A 用户能看到 B 用户对话 |
| **会话路由** | ❌ 前端写死 `conversation_id="web-conversation"` | 所有用户共享同一会话 |
| **限流** | ❌ QuotaMiddleware 写好但默认未启用 | 一个脚本能打爆服务 |
| **多副本** | ❌ InMemoryLockManager + 单进程 SQLite | 横向扩展会丢数据 |
| **真实可观测性** | ❌ JSONL 落地了,OTel 没接 collector | 出问题查不了线 |
| **数据备份** | ❌ 没有 | SQLite 损坏 = 历史全没 |
| **PII 脱敏** | ❌ 没有 | 用户敏感信息原样进 LLM 上游 / 入库 / 入日志 |
| **prompt 注入防护** | ❌ 没验证过 | 用户可能 jailbreak system prompt |
| **审计日志** | ❌ 没有 | 无法回答"谁在 X 时间问了什么" |
| **数据保留策略** | ❌ 没有 | GDPR/隐私法律要求"删除我的数据"无法响应 |

---

## 1. 上线门槛分级

不同的"上线"含义不同,门槛差很远。**先想清楚是哪种上线**,再看对应清单。

### Tier 0 — 团队内部技术演示(✅ 现在就能做)

- 1-2 个开发者临时演示
- 不接受外部流量
- 没有真实业务数据
- 出问题就重启

**当前状态满足。不需要做任何额外工作。**

### Tier 1 — 团队内 alpha 试用(<10 人,需要 2 周)

- 信任的同事用真实问题试,愿意忍受 bug
- 数据敏感度:低(测试性问题)
- 期望:服务大体稳定,能用就行
- **必须**:至少有认证 + 会话隔离

→ 见 §2 P2 必做项

### Tier 2 — 部门级试用(<50 人,需要 4-5 周)

- 部门内非开发同事日常使用
- 数据敏感度:中(可能涉及内部资料)
- 期望:基本不出事故,出了能定位
- **必须**:Tier 1 + 部署 + 监控 + 备份

→ 见 §2 P2 + §3 P3

### Tier 3 — 全公司 / 涉及客户数据(需要 8-10 周以上)

- 全员可用,涉及客户/财务/HR 等敏感数据
- 数据敏感度:高
- 期望:零数据泄露,合规可审计
- **必须**:Tier 2 + 多副本 + PII 脱敏 + 审计 + 合规

→ 见 §2 P2 + §3 P3 + §4 P4

---

## 2. P2 致命漏洞修复(2 周,Tier 1 必做)

### 2.1 认证 — 哪怕最简单的也比裸奔强

**现状:** `/api/runs/stream`、`/api/conversations/{id}/messages` 不需要任何凭证,curl 一下就能调。

**最小可行方案:**

- 如果公司有 SSO(OIDC/OAuth2/SAML),用网关或 FastAPI middleware 校验 JWT
- 没有 SSO 的过渡方案:在前置反向代理(nginx/Caddy)做 Basic Auth + IP allowlist
- **不要**把 API key 自己重新发明一遍——会写错

**实现:**

1. `services/web_api.py` 加一个 `Depends(verify_token)`,从 header 取 token、解码、塞到 `request.state.user`
2. 整个 router 加上这个 dependency
3. 把 `user` 透传到 `AgentContext.user`,后续审计能用

**验证:**
- `curl /api/runs/stream` 没 token 应当返回 401
- 有效 token 能正常用

### 2.2 多租户隔离 — 把 TenantContext 真用起来

**现状:** `agentengine/enterprise/tenant.py` 已实现 `TenantContext`,但 `web_api` 没构造它,`AgentContext.extras["tenant"]` 始终为空。SQLite 也没按 tenant 分表/分 schema。

**改动清单:**

1. **认证拿到 user_id 后,在 web_api 构造 TenantContext** 并塞到 `context.extras["tenant"]`
2. **SQLite schema 加 `tenant_id` 列**,`messages` / `runs` / `artifacts` 三张表都加
3. **`SqlitePersistence.load_messages` / `save_messages` 都按 tenant_id 过滤**
4. **conversation_id 加上 tenant 前缀**或独立列,防止跨租户撞 id

**风险:** 这是 schema 变更,要写迁移脚本。当前 `data/chatbot.db` 还都是 demo 数据,可以直接删库重建。

**验证:**
- 写一个集成测试:tenant_a 写一条消息,tenant_b 用同 conversation_id 读不到
- 真实 e2e:两个 token 互相看不到对方历史

### 2.3 会话路由 — 前端 conversation_id 真正生效

**现状:** `web/src/traceTransport.ts` line 33 写死 `conversation_id="web-conversation"`,所有人共享一个 conversation。前端"新建会话"只是清屏,后端 DB 里始终是那一个 conversation。

**改动清单:**

1. **前端**:用 `crypto.randomUUID()` 在前端生成 conversation_id,塞 localStorage
2. **新建会话** = 生成新 UUID 给当前 session
3. **会话列表** = `/api/conversations?limit=20` 返回该 user 的最近会话(后端要补这个端点)
4. **点击侧边栏会话**:fetch `/api/conversations/{id}/messages` 重装 trace UI(端点已经写好,前端没用)

**验证:**
- 两个浏览器标签页,各自新建会话,互不干扰
- 关浏览器再打开,localStorage 恢复 conversation_id,刷新能看到历史

### 2.4 限流 — 防止单用户烧爆服务

**现状:** `QuotaMiddleware` 实现完整但默认未启用。`max_runs / max_tool_calls / max_tokens_in / max_tokens_out` 都是 0(unlimited)。

**最小配置:**

```yaml
# config/quota.yaml(新建)
defaults:
  max_runs_per_minute: 20
  max_tool_calls_per_minute: 60
  max_tokens_in_per_day: 100000
  max_tokens_out_per_day: 50000
```

1. **启动时给所有 tenant 设置默认 limits**
2. **TurnRunner 注入 MiddlewareChain([quota_middleware(QUOTA_STORE)])**
3. **超限时给前端返 `error` 帧,code=`quota_exceeded`,UI 友好提示**

**注意:** 当前 `QuotaStore` 是内存版,重启失效。Tier 1 临时可以接受;Tier 2 起必须换 Redis。

### 2.5 基础部署 — Dockerfile + docker-compose

**现状:** 只有 `pyproject.toml`,部署需要手搓 venv + 装依赖 + 写 systemd unit。

**交付物:**

1. **Dockerfile**(后端):基于 `python:3.12-slim`,COPY src/ 装依赖,默认 CMD 跑 uvicorn
2. **Dockerfile**(前端):多阶段,vite build + nginx serve dist/
3. **docker-compose.yml**:
   - `backend`(FastAPI 8000)
   - `frontend`(nginx 80)
   - `volumes`:`./data:/app/data`(SQLite)、`./logs:/app/logs`(JSONL)
4. **`.env.example`**:列出必需 env var

**验证:**
- `docker compose up -d` 一行起服务
- 重启容器,`data/chatbot.db` 历史保留

### P2 阶段交付清单

- [ ] 认证 middleware + token 校验 + 测试
- [ ] SQLite 加 tenant_id,迁移脚本(或重置 demo 库)
- [ ] 会话列表 endpoint + 前端会话路由
- [ ] QuotaStore 默认启用 + 配置文件
- [ ] Dockerfile + docker-compose.yml
- [ ] 文档:部署 README,环境变量说明
- [ ] 跑一次"两个用户互相看不见对方历史"的集成 e2e

完成 P2 = **可邀请 5-10 个信任同事 alpha 试用**(Tier 1)。

---

## 3. P3 上线必备(再 2 周,Tier 2)

### 3.1 真实可观测性 — 不仅是日志,要能看趋势

**现状:** `OTelTracingMiddleware` 写好了但没人接 collector。`logs/runs/<日期>/run_*.jsonl` 落盘了但没人看。

**部署目标:**

1. **OTel Collector** — docker-compose 加一个 `otel-collector` 服务,把 spans 推到 Grafana Tempo 或 Jaeger
2. **基础看板**:
   - **黄金信号**:RPS、P50/P95/P99 延迟、错误率
   - **业务指标**:每用户 token 用量、tool 调用次数、平均轮次
   - **故障告警**:错误率 > 1% / P99 > 30s / LLM 502 持续 5 分钟
3. **日志聚合**:JSONL 推到 Loki / Elasticsearch,能按 run_id 查
4. **健康检查升级**:`/api/health` 检查 LLM 可达性、DB 可写、磁盘空间

**最低标准:** 你能在 Grafana 看到当前在线用户数、过去 1 小时的 token 消耗、最慢的 5 个 run。

### 3.2 数据备份 — SQLite 不能裸奔

**现状:** `data/chatbot.db` 写在容器里,容器重建就丢。

**方案分两档:**

**轻量(Tier 2 起步):**
- 每 15 分钟 `sqlite3 .backup` dump 到对象存储(MinIO / S3)
- 保留最近 7 天 + 每天最后一个 + 每周最后一个
- 用脚本 + cron,docker-compose 加个 backup 容器

**重量(Tier 3 必做):**
- 切换到 Postgres
- DB 自身 PITR + 定期 dump

### 3.3 真实压力测试

**现状:** 没人压过。50 人并发会怎样?不知道。

**计划:**
1. 用 `locust` 或 `k6` 模拟:
   - 50 个虚拟用户,每个 30 秒发一条消息
   - 持续 30 分钟
2. 观察:
   - 错误率 < 1%
   - P99 延迟 < 30s
   - 内存稳定(没泄漏)
   - DB 写入不堵
3. 暴露问题就修,典型嫌疑:
   - SQLite 写锁竞争(切到 Postgres 或加 WAL 调优)
   - asyncio 事件循环阻塞(找哪里有同步 IO)
   - 连接池耗尽(httpx client、DB connection)

### 3.4 部署运维基础

- [ ] **Runbook**:服务挂了怎么救、备份怎么恢复、key 泄漏怎么换、回滚怎么做
- [ ] **磁盘清理**:`logs/runs/` 老于 7 天自动清掉
- [ ] **服务自启**:容器 restart=always,系统级 systemd 兜底
- [ ] **优雅关停**:SIGTERM 后等 30 秒,让正在跑的 SSE 连接完成
- [ ] **配置外部化**:env var 全部能从 secret manager 注入(Vault / K8s Secret)
- [ ] **stop 按钮**:前端"停止生成"按钮 → 取消 SSE 连接 → 后端取消 task

### 3.5 prompt 注入与异常输入防护

**现状:** 用户输入直接拼进 messages,没有任何预处理。

**至少做:**

1. **长度限制**:已有(`max_query_chars=20000`),保持
2. **明显的注入检测**:简单 regex 拒绝 "ignore previous instructions" / "system prompt is" 等模式(只是简单兜底,真防御靠 LLM 本身)
3. **工具调用白名单**:每个 agent 显式声明允许的 tool,拒绝 LLM 调用清单外的 tool
4. **destructive 工具加 ApprovalGate**:已有机制,默认对 `is_destructive=True` 的工具启用

### P3 阶段交付清单

- [ ] OTel Collector 部署 + Grafana 看板
- [ ] 日志聚合 + 告警规则
- [ ] SQLite 定时备份脚本 + 恢复演练
- [ ] locust 压力测试报告
- [ ] Runbook
- [ ] 磁盘清理 cron
- [ ] 优雅关停 + stop 按钮
- [ ] 简单 prompt 注入兜底

完成 P3 = **可对部门级 50 人开放试用**(Tier 2)。

---

## 4. P4 长期演进(Tier 3 / 持续运营)

### 4.1 多副本部署

**现状:** `InMemoryConversationLockManager` 单进程内有效,多副本失效。

**方案:**
1. **锁管理器换 Redis**:`RedisConversationLockManager(redis_client)`,实现 `SET NX EX 30s` + 心跳续期
2. **持久化换 Postgres**:把 `SqlitePersistence` 的 SQL 翻译成 asyncpg,schema 不变
3. **会话亲和**:不需要——因为锁已分布式化
4. **负载均衡**:k8s service / nginx upstream

### 4.2 数据合规

- **PII 脱敏**:
  - 入库前过 detector(simple regex 起步,真要做就接 Microsoft Presidio)
  - 日志里 mask 掉
  - 上游 LLM 调用前可选脱敏(看公司政策)
- **数据删除**:支持 `DELETE /api/users/{id}/data`,级联清掉所有 conversation/run/artifact
- **数据保留策略**:超过 N 天的 conversation 自动归档/删除,可按 tenant 配置
- **审计日志**:每次 destructive 操作 / 数据导出 / 配置变更都记一条带签名的审计 entry

### 4.3 模型治理

- **多模型路由**:不同 agent 用不同 model,按延迟/成本/质量切换
- **A/B 测试框架**:同一个 agent 跑两个 prompt 版本,统计满意度
- **评测集 + 回归测试**:每次改 prompt / 换模型,跑评测集看效果有没有退化
- **成本核算**:按 tenant / user / agent 维度核算 token 消耗

### 4.4 二次开发支持

- **公开 API 文档**:OpenAPI / Postman collection
- **SDK**:Python / TypeScript 客户端,封装 SSE 流和会话管理
- **插件机制**:让业务团队不改框架就能加自己的 tool / agent / SKILL.md

---

## 5. 时间表(以一个工程师 100% 投入为基准)

```
Week 1-2  P2 致命漏洞修复
          ├─ 认证 + 多租户(3 天)
          ├─ 会话路由(2 天)
          ├─ 限流启用 + 配置(1 天)
          └─ Docker 化 + docker-compose(2 天)
          ===========================
          完成 → Tier 1 可上线(10 人 alpha)

Week 3-4  P3 上线必备
          ├─ OTel + Grafana 接入(3 天)
          ├─ 备份 + 恢复演练(2 天)
          ├─ 压力测试 + 调优(2 天)
          └─ Runbook + 优雅关停 + 防护(3 天)
          ===========================
          完成 → Tier 2 可上线(50 人内部)

Week 5+   P4 长期演进(按需推进)
          ├─ 多副本(Redis + Postgres,1 周)
          ├─ 数据合规(PII / 审计 / 保留,2 周)
          ├─ 模型治理(评测 / 路由,持续)
          └─ SDK + 插件(持续)
```

**强烈不建议跳过 P2 直接给真实用户用。** 没认证的 API 一旦泄露 URL,几小时就能烧光 LLM 配额或泄露所有用户对话。

---

## 6. 决策点

在开始 P2 前,有几件事需要团队拍板:

1. **认证用什么** — 公司有 SSO 吗?走 OIDC / OAuth2 / SAML?没有的话临时用什么?
2. **数据库目标** — SQLite 撑到几人就切 Postgres?还是一开始就上 Postgres?
3. **部署目标平台** — 自建 K8s?docker-compose 单机?公司有现成的 PaaS 吗?
4. **可观测性栈** — 公司已有 Grafana / Loki / Elasticsearch?还是要现搭?
5. **合规要求** — 是否需要满足等保 / GDPR / SOC2?这决定了 P4 的优先级
6. **目标用户规模与时间** — 想几月几号开放给多少人?这决定了 P2-P4 的紧迫度

每一项的答案都会显著影响 P2-P4 的具体实现路径。

---

## 7. 一句话总结

> **当前状态:架构干净的 PoC,可做技术演示,不可对真实用户开放。**
>
> **距离 Tier 1(10 人 alpha)2 周。距离 Tier 2(50 人内部)4-5 周。距离 Tier 3(全公司或对外)8-10 周以上。**
>
> **千万不要跳过认证和多租户直接上线。**
