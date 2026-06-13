# AgentEngine 整体架构图

```mermaid
flowchart TB
    %% ========== Styles ==========
    classDef client fill:#eef6ff,stroke:#6aa7e8,stroke-width:1.5px,color:#1f2d3d
    classDef service fill:#f6f0ff,stroke:#a983ed,stroke-width:1.5px,color:#1f2d3d
    classDef runtime fill:#ecfaf6,stroke:#67bfa7,stroke-width:1.5px,color:#1f2d3d
    classDef loop fill:#fff7e8,stroke:#e8a94f,stroke-width:1.5px,color:#1f2d3d
    classDef tool fill:#fff0ee,stroke:#dc7f74,stroke-width:1.5px,color:#1f2d3d
    classDef output fill:#f1f5f9,stroke:#8aa0b6,stroke-width:1.5px,color:#1f2d3d
    classDef store fill:#eef9ef,stroke:#77ba83,stroke-width:1.5px,color:#1f2d3d

    %% ========== 01 Entry ==========
    subgraph L1["01 接入层"]
        Web["React Web UI<br/>app/frontend/src<br/>消费 SSE，聚合 RunTrace"]:::client
        CLI["CLI / Demo<br/>scripts/chat_pretty.py<br/>examples/full_sdk_example.py"]:::client
        Host["宿主系统<br/>业务系统直接嵌入 SDK<br/>自定义 definition / LLM / persistence"]:::client
        Definitions["Agent Definitions<br/>general_chat / deep_research<br/>Markdown(frontmatter) 或 Python<br/>instructions + tools + setup hooks"]:::client
    end

    %% ========== 02 Service ==========
    subgraph L2["02 服务与 SDK 门面"]
        API["FastAPI Web API<br/>/api/runs/stream<br/>/capabilities / approvals"]:::service
        Orchestration["AgentOrchestrationService<br/>示例服务：注册 definitions，读取环境 LLM<br/>共享 SQLite、锁、企业中间件"]:::service
        Engine["AgentEngine<br/>SDK facade<br/>校验输入 / 解析 RunConfig<br/>注入 LLM / persistence / locks"]:::service
    end

    %% ========== 03 Runtime ==========
    subgraph L3["03 运行编排层"]
        Middleware["MiddlewareChain<br/>otel tracing<br/>quota / retry / approval"]:::runtime
        Runner["TurnRunner<br/>生成 run_id / turn_id<br/>维护 RunState 与终态分类<br/>安装 FileAccessTracker<br/>事件扇出到回调和日志"]:::runtime
        Hooks["HookManager<br/>SessionStart / UserPromptSubmit<br/>PreToolUse / PostToolUse / Stop"]:::runtime
        Control["并发与取消<br/>ConversationLockManager<br/>CancellationToken / interrupt"]:::runtime
    end

    %% ========== 04 ReAct Loop ==========
    subgraph L4["04 单轮 ReAct 循环"]
        Memory["Memory<br/>有序消息历史<br/>system / user / assistant / tool<br/>load_from_db / save_to_db"]:::loop
        Compactor["Compactor<br/>LLMSummaryCompactor<br/>超过阈值时压缩旧上下文"]:::loop
        RunTurn["run_turn()<br/>单一 think / act 循环<br/>1. setup 后写入用户消息<br/>2. 自动压缩上下文<br/>3. 调用 LLM streaming<br/>4. 执行 tool_calls 并回写工具消息<br/>5. 无工具调用时结束并上报 usage"]:::loop
        LLM["LLMClient<br/>chat / chat_stream 协议<br/>OpenAICompatibleClient<br/>输出 text / reasoning / tool_calls"]:::loop
        Executor["ToolExecutor<br/>timeout / streaming / policy<br/>result summary"]:::tool
        Collection["ToolCollection<br/>注册工具实例<br/>导出 OpenAI tool schema"]:::tool
        Builtins["Built-in Tools<br/>bash / grep / read_file<br/>write_file / edit_file / Skill<br/>TodoWrite / AskUserQuestion"]:::tool
    end

    %% ========== 05 Outputs ==========
    subgraph L5["05 输出、存储与外部依赖"]
        SSE["SSE Stream<br/>Printer -> SseEventQueue -> Web UI"]:::output
        Logs["JSONL Logs<br/>logs/runs/yyyy-mm-dd/*.jsonl"]:::output
        SQLite["SQLite<br/>messages / runs / artifacts"]:::store
        Workspace["Workspace<br/>内置工具读取和修改文件"]:::store
        Provider["LLM Provider<br/>OpenAI-compatible API"]:::output
    end

    %% ========== Main Flow ==========
    Web --> API
    CLI --> Orchestration
    Host --> Engine
    Definitions --> Engine
    API --> Orchestration --> Engine
    Engine --> Middleware --> Runner
    Engine --> Control
    Runner --> Hooks --> Control
    Runner --> RunTurn

    %% ========== ReAct Loop ==========
    RunTurn <--> Memory
    Memory --> Compactor
    RunTurn --> LLM
    LLM --> RunTurn
    LLM <--> Collection
    RunTurn --> Executor
    Executor --> Builtins
    Builtins --> Workspace
    LLM --> Provider
    Executor --> RunTurn
    Executor --> Memory

    %% ========== Events / Persistence ==========
    Runner --> SSE
    Runner --> Logs
    Memory --> SQLite
    API --> SSE
    SSE --> Web

    %% ========== Notes ==========
    Runner -. "同步边界：每个 conversation 加锁" .-> Control
    Middleware -. "治理边界：quota / approval / retry / tracing" .-> Engine
    RunTurn -. "主闭环：LLM 产生 tool_calls，工具结果回写 Memory" .-> Memory
```

## 核心链路

1. Web、CLI 或宿主系统把用户请求交给 `AgentEngine`。
2. `AgentEngine` 解析 definition / `RunConfig`，注入 LLM、持久化、锁和中间件。Definition 可用 Python 构造，也可用 Markdown（YAML frontmatter + 正文）声明，经 `load_definition` / `load_definitions` 编译为 `AgentDefinition`（frontmatter 的 `tools` 列表会自动装配内置工具）。
3. `TurnRunner` 包住一次运行，负责运行 ID、状态、事件扇出、日志和 hook 生命周期。
4. `run_turn()` 执行单一 ReAct 循环：读写 `Memory`，调用 `LLMClient`，执行 `tool_calls`，再把工具结果写回 `Memory`。
5. 运行事件同时进入 SSE、JSONL 日志和外部回调；历史消息通过 SQLite 持久化。
