# AgentEngine Agent Skills 标准化改造计划

> 实施基线：本文第 10 节之后的“实施契约修订”是评审后的最终约束。
> 若前文章节与修订内容冲突，以修订内容为准；实施前应同步回写对应阶段，避免保留两套契约。

## 1. 总体目标

将当前以 `SKILL.md` 提示词注入为主的 skill 系统升级为完整能力包系统：

1. 支持 `SKILL.md`、`scripts/`、`references/`、`assets/`。
2. 实现 Catalog、Instructions、Resources 三层 Progressive Disclosure。
3. 允许模型发现、激活、读取和执行 skill。
4. skill 脚本只能在会话 Sandbox 内执行。
5. 激活后的 skill 在裁剪、压缩和持久化后仍然有效。
6. 同时兼容 `.agents/skills/` 和旧 `.agent/skills/`。

## 2. 关键设计约束

- 保留现有 `Skill.path`、`Skill.body`、`Skill.prompt` 和 `SkillTool` 调用方式。
- Catalog 只暴露 enabled skills。
- 激活 skill 时只加载 `SKILL.md` 和资源清单，不预读全部资源。
- 资源路径统一使用 POSIX 相对路径。
- 拒绝绝对路径、`..` 和解析后逃逸 skill 根目录的 symlink。
- `ExecPolicy` 负责准入和审批，不作为代码执行安全边界。
- skill 脚本不得通过宿主机 `BashTool` 执行。
- 上下文保护依赖 `Message.metadata`，不依赖 XML 正则判断。
- 保护 skill 激活消息时必须保留 assistant tool call 与 tool result 配对。
- `Message.metadata` 必须能被持久化层完整保存和恢复，否则重启后上下文保护失效。
- `SkillTool` 的 schema enum 和 description 是 per-conversation 动态生成的，禁止跨会话共享同一份可变 schema。
- frontmatter `name` 与父目录名不一致时给出明确兼容策略。

## 3. 分阶段交付

| 阶段 | 交付内容 | 风险 | 建议批次 |
|---|---|---:|---|
| Phase 0 | 固定数据契约、安全边界、路径规则、frontmatter `name` 校验 | 低 | 第一批 |
| Phase 1 | Skill 数据模型和资源发现 | 低 | 第一批 |
| Phase 2 | SkillTool 结构化激活结果 | 低 | 第一批 |
| Phase 4 | `.agents/skills/` 路径兼容 | 低 | 第一批 |
| Phase 3 | Enabled Skill Catalog（tool description + 可选 system prompt） | 中 | 第二批 |
| Phase 5 | 激活状态、裁剪和压缩保护（含 metadata 持久化） | 中 | 第三批 |
| Phase 6 | Skill 资源安全读取 | 中 | 第四批 |
| Phase 7 | Sandbox 脚本执行 | 高 | 第五批 |
| Phase 8 | 公共 API、文档和端到端验证 | 低 | 随各批次完成 |

## 4. 按文件夹分类的任务

### 4.0 Phase 0：数据契约、安全边界和路径规则

在改动任何 skill 发现/激活逻辑前，先固定以下契约，避免后续阶段返工。

#### Message 持久化契约

- `Message` 新增 `to_persistent()` 和 `from_persistent()`，序列化/反序列化完整字段，包括 `metadata`。
- `Message.to_openai()` 保持只发送 LLM API 需要的字段，**不输出 `metadata`**。
- `SqlitePersistence.save_messages()` 改用 `Message.to_persistent()` 生成 `payload`，而不是 `to_openai()`。
- 旧数据库中的 payload 如果没有 metadata，按空 metadata 兼容处理。

#### frontmatter `name` 与目录名一致性

- 标准（agentskills.io）要求 `name` 与父目录名一致，且只能包含小写字母、数字和连字符。
- 当前 loader 会把 `_` 规范化为 `-`，导致不一致。
- 本阶段明确策略：
  - `.agents/skills/` 使用严格模式，不一致时跳过并记录错误。
  - 旧 `.agent/skills/` 使用兼容模式，不一致时 warning 后继续加载。
  - 显式 roots 可通过 `strict_validation` 指定行为。
  - `scaffold()` 生成的目录名和 `name` 必须一致。

#### 路径安全基础规则

- 所有 skill 资源路径使用 POSIX 相对路径（`scripts/extract.py`）。
- 拒绝空路径、绝对路径、`..`、解析后逃逸 `base_dir` 的 symlink。
- `scripts/`、`references/`、`assets/` 为固定保留目录名，其他目录不视为资源。
- 单个 skill 资源数量上限默认 100 个文件（可配置）。
- 超限时该 skill discovery 失败并返回明确错误，不进入 Catalog 或可激活集合。

#### `persistence/sqlite.py`

Phase 0：

- `save_messages()` 改用 `Message.to_persistent()` 生成 `payload`，而不是 `to_openai()`。
- `load_messages()` 返回的 dict 列表通过 `Message.from_persistent()` 恢复。
- 旧数据库中缺少 `metadata` 的 payload 按空 metadata 兼容处理。
- 不修改 messages 表 schema，仅改变 payload 的序列化内容。

#### `persistence/port.py`

Phase 0：

- 更新 `PersistencePort.save_messages()` / `load_messages()` 文档契约：payload 是 AgentEngine 持久化消息格式，不再等同于 OpenAI API 消息格式。
- payload 增加 `"_format": "agentengine.message.v1"` 版本字段。
- 更新所有 fake、mock 和第三方 Persistence 的契约测试。
- 加载无 `_format` 的旧 payload 时按现有 OpenAI 消息格式兼容解析。
- Web API 对外输出会话消息时过滤内部 `_format` 和 `metadata`。
- 默认不持久化完整 `base64_image`，改存文件或对象存储引用。

### 4.1 `src/agentengine/skills/`

#### `loader.py`

Phase 1：

- 新增 `SkillResources` 数据类：

```python
@dataclass(frozen=True, slots=True)
class SkillResources:
    scripts: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
```

- 扩展 `Skill`：
  - `base_dir: Path`
  - `resources: SkillResources`
- 保留 `path`、`body`、`prompt`（向后兼容）。
- 新增 `activation_content(args: str = "") -> str`：
  - 生成 Phase 2 所需的结构化 XML 激活内容。
  - 替换 `${ARGUMENTS}`。
  - 包含 skill directory、instructions、resources 清单。
  - 对 name、description、body、路径中的 XML 特殊字符做转义。
- 新增 `list_resources(skill_dir)`。
- 递归枚举：
  - `scripts/**`
  - `references/**`
  - `assets/**`
- 忽略隐藏文件、缓存目录、目录本身和 `SKILL.md`。
- 输出稳定排序的 POSIX 相对路径。
- 限制单个 skill 的资源数量（默认单个 skill 最多 100 个资源文件，可配置）。
- 拒绝解析后逃逸 `base_dir` 的资源。
- `Skill.prompt` 保持现有行为，供旧代码和测试使用；新代码优先使用 `activation_content()`。

Phase 4：

- 默认扫描顺序调整为：
  1. `project/.agents/skills`
  2. `project/.agent/skills`
  3. `user/.agents/skills`
  4. `user/.agent/skills`
- 同名 skill 由先扫描到的高优先级目录覆盖。
- 显式传入 `roots` 时不追加默认目录。
- `scaffold()` 默认写入 `.agents/skills/`。

#### `catalog_prompt.py`（新增）

Phase 3：

- 新增 `SkillCatalogPrompt`。
- 输入 enabled `Skill` 集合。
- 输出仅包含 `name` 和 `description` 的 Catalog。
- 无 enabled skill 时返回空内容。
- 保证稳定排序并正确转义特殊字符。
- 同时提供两种 Catalog 暴露方式，由宿主应用选择：
  1. **SkillTool.description + schema enum**（默认推荐）：把 Catalog 注入工具描述，限制模型只能调用 enabled skills。
  2. **System prompt Catalog 注入**（可选）：在会话开始时把 Catalog 作为 system message 注入，适合 tool description 长度受限或模型对 tool description 理解不稳定的场景。

示例：

```xml
<available_skills>
  <skill>
    <name>data-analysis</name>
    <description>Analyze structured files and produce reports.</description>
  </skill>
</available_skills>
```

两种方式的实现要求：
- SkillTool.description + schema enum：
  - `SkillTool` 实例是 per-conversation 的，每个 conversation 根据当前 enabled skills 生成新的 `SkillTool` 实例。
  - 或者重写 `SkillTool.to_openai_tool()` 方法，在调用时动态读取 enabled skills 并生成 schema enum，避免修改实例属性导致跨会话污染。
  - 禁用 skill 不出现在 description 和 enum 中。
- System prompt Catalog 注入：
  - 通过 `RunConfig` / `AgentDefinition` 的 `enable_skill_catalog: bool = False` 控制，允许单个 agent 覆盖。
  - 在 `run_turn()` 中 `await agent.setup()` 之后、历史消息加载之前注入，作为独立 system message。
  - 注入时附带简短行为指令，告诉模型如何激活 skill。
  - 当 `enable_skill_catalog=True` 时，SkillTool.description 中不再重复完整 Catalog，避免冗余。

#### `paths.py`（新增）

Phase 6、7：

- 集中实现 skill 路径校验。
- 拒绝空路径、绝对路径和 `..`。
- 使用 `resolve()` 验证目标仍位于 `base_dir`。
- 校验目标是已声明资源，而不是任意 skill 目录文件。
- 为资源读取和脚本执行共享同一套安全规则。

#### `materializer.py`（新增）

Phase 7：

- 新增 `SkillMaterializer`。
- 将 skill 包复制到会话 workspace：

```text
/workspace/.skills/<skill-name>/
```

- 默认只读物化 skill 文件。
- 使用 manifest 或内容 hash 判断是否需要刷新。
- 不把任意宿主机 skill 目录直接以可写方式挂载到容器。
- 不复制越界 symlink。
- 将运行产物写入 `/workspace/outputs` 等独立目录。

#### `__init__.py`

- 导出需要公开或跨模块使用的类型：
  - `Skill`
  - `SkillResources`
  - `SkillLoader`
  - `SkillCatalogPrompt`

### 4.2 `src/agentengine/tools/builtin/`

#### `skill_tool.py`

Phase 2：

- 保留工具名 `Skill` 和现有参数。
- `SkillTool.run()` 内部调用 `loaded_skill.activation_content(args)`，不再使用旧 `Skill.prompt` 拼接字符串。
- 返回结构化激活内容：

```xml
<skill_content name="data-analysis">
  <skill_directory>...</skill_directory>
  <instructions>...</instructions>
  <skill_resources>
    <scripts>...</scripts>
    <references>...</references>
    <assets>...</assets>
  </skill_resources>
</skill_content>
```

- `${ARGUMENTS}` 继续替换。
- 正确转义正文、名称和路径。
- 不在激活时读取全部资源正文（只返回 resources 清单）。
- disabled 和 unknown skill 行为保持兼容。
- 旧代码若直接读取 `Skill.prompt`，行为不变。

Phase 3：

- 工具描述动态包含 enabled skill Catalog。
- `schema.properties.skill.enum` 动态列出 enabled skill 名称。
- disabled skill 不进入描述或 enum。
- skill 列表刷新后能够重建 Catalog。

Phase 5：

- SkillTool 执行结果携带：

```python
{
    "skill_name": "data-analysis",
    "skill_activation": True,
    "protected": True,
}
```

#### `skill_resource_tool.py`（新增）

Phase 6：

- 新增 `ReadSkillResource`。
- 参数：
  - `skill`
  - `path`
- skill 必须存在并启用。
- path 必须出现在 `SkillResources` 中。
- 文本资源设置大小限制和截断策略。
- 二进制 asset 返回元信息或文件引用，不强制解码为文本。
- 不赋予模型读取任意宿主机文件的能力。

#### `skill_script_tool.py`（新增）

Phase 7：

- 新增 `RunSkillScript`，不让普通 Bash 自动猜测 skill 上下文。
- 参数：
  - `skill`
  - `script`
  - `args: list[str]`
  - `timeout`
- script 必须存在于 `resources.scripts`。
- 使用参数数组，避免模型拼接完整 shell 命令。
- 调用 `SkillMaterializer` 后通过 Sandbox 执行。
- 禁止回退到宿主机 `BashTool`。
- 返回 stdout、stderr、exit code、timeout 状态和输出文件信息。

#### `__init__.py`

- 导出新增工具。
- 明确哪些工具进入默认工具集，哪些必须由宿主应用显式注册。

### 4.3 `src/agentengine/memory/`

#### `message.py`

Phase 0：

- 新增 `Message.to_persistent() -> dict[str, Any]`：
  - 序列化 `role`、`content`、`reasoning_content`、`name`、`tool_call_id`、`tool_calls`、`base64_image`、`metadata` 等全部字段。
- 新增 `Message.from_persistent(data: dict[str, Any]) -> Message`：
  - 完整恢复上述字段，包括 `metadata`。
- `to_openai()` 保持只输出 LLM API 所需字段，**不输出 `metadata`**。

Phase 5：

- 让 `Message.tool()` 接受 `metadata` 参数。
- `from_openai()` 保持只处理 OpenAI API 返回的字段；metadata 恢复走 `from_persistent()`。
- 不把内部 metadata 直接发送给不支持该字段的模型 API。

#### `memory.py`

Phase 5：

- `_trim()` 识别受保护的 skill 激活消息。
- 新增 `extend_atomic()` 或等价延迟裁剪机制，完整追加 assistant tool call 与 tool result 后再执行 trim。
- 同时保护 assistant tool call 和对应 tool result。
- message count 和 token budget 两种裁剪都不得产生孤立 tool message。
- 同名 skill 多次激活时只保留最新有效激活对。
- 保护消息仍计入 token 预算；预算不足时明确报错，不能静默丢失。

### 4.4 `src/agentengine/runtime/`

#### `turn.py`

Phase 5：

- `ToolOutput.metadata` 经 `ToolExecutor` 解包到 `ToolExecutionResult.metadata`，再传入 `Message.tool()`。
- SkillTool 的激活 metadata 最终进入 memory。
- 调整工具消息写入时机，确保 tool-call group 完整后通过原子接口进入 memory。
- 普通工具结果行为不变。

#### `compaction.py`

Phase 5：

- 按 metadata 识别受保护的 skill 激活消息。
- 不通过 `<skill_content>` 文本匹配。
- 压缩前先把 protected skill 激活消息对（assistant tool call + tool result）从 messages 中提取出来。
- 压缩结果顺序：
  1. 原 system messages
  2. compaction summary
  3. 受保护的 skill 激活消息对（按原顺序）
  4. recent messages
- 同名 skill 激活记录去重：只保留最近一次的激活对。
- 避免 protected 消息同时进入摘要和原文，造成重复上下文。
- 验证压缩后的 OpenAI tool-call 序列合法：每个 tool_call_id 都有对应的 assistant tool call 和 tool result。
- 如果没有非 protected 历史可压缩，但 protected 消息存在，直接返回原 messages，避免无意义调用 LLM。

#### `turn_runner.py`

- 原则上不承担 Catalog 注入。
- 只有在后续确认动态 `SkillTool.description` 无法满足模型适配时，才增加可选 system Catalog 注入点。

### 4.5 `src/agentengine/tools/`

#### `executor.py`

Phase 5：

- 扩展 `ToolExecutionResult`：

```python
metadata: dict[str, Any] = field(default_factory=dict)
```

- 新增 `ToolOutput` 作为工具业务返回对象，允许工具返回 content、metadata 和 raw。
- `ToolExecutor` 解包 `ToolOutput` 后自行创建 `ToolExecutionResult`，不得接受工具直接返回执行器结果对象。
- 保持字符串返回值向后兼容（字符串结果 metadata 为空）。
- 不在 `ToolExecutor` 中实现 skill bash 自动转发。

#### `builtin/bash_tool.py`

- 不直接支持执行 skill 脚本。
- 保持宿主机可信场景的现有用途。
- 文档明确其不是 skill 执行安全边界。

### 4.6 `src/agentengine/sandbox/`

#### `session_sandbox.py`

Phase 7：

- 增加受控 `workdir` 参数。
- workdir 必须位于容器 `/workspace` 下。
- 优先新增 `exec_argv()`，减少 shell 字符串注入面。
- `exec_shell()` 继续服务普通 Sandbox Bash。
- 保持 timeout、stdout/stderr 和 exit code 行为。

#### `tools.py`

Phase 7：

- 为 `RunSkillScript` 提供 Sandbox 执行入口。
- 使用 argv 传参。
- 禁止调用宿主机 subprocess。
- 保持每个 conversation 独立容器。

#### `manager.py`

Phase 7：

- 提供当前 conversation 的宿主机 workspace 路径。
- 协调 skill 物化目录生命周期。
- conversation release 时继续清理 workspace 和 skill 副本。

#### `config.py`

Phase 7：

- 如有必要增加：
  - skill 物化子目录配置
  - 最大执行超时
  - 最大资源大小
  - 输出目录
- 保持 Sandbox 默认无网络、资源限制和只读根文件系统策略。

### 4.7 `src/agentengine/engine.py`

Phase 3：

- 不在 `AgentEngine.run()` 中直接读取最终 ToolCollection 或注入 Catalog，因为此时 preset setup 尚未完成。
- `AgentEngine` 仅透传 `RunConfig` / `AgentDefinition` 的 `enable_skill_catalog` 配置。
- 实际 system Catalog 注入由 `run_turn()` 在 `agent.setup()` 之后完成。
- 关闭时 Catalog 完全由 `SkillTool.description` 暴露；开启时避免两处重复完整 Catalog。
- 保持 `AgentEngine` 其他公共运行流程不变。

### 4.8 `app/backend/services/`

#### `web_api.py`

Phase 3：

- 保留 `_apply_skill_directive()`，服务用户显式选择 skill 的场景。
- enabled skill 集合同时用于：
  - SkillTool allow-list
  - Catalog
  - schema enum
- 没有 enabled skill 时可不注册 SkillTool。

Phase 6、7：

- 注册 `ReadSkillResource`。
- 只有可用 `SandboxManager` 时注册 `RunSkillScript`。
- 不允许生产 Web API 在缺少 Sandbox 时回退到宿主机执行。

#### `agent_orchestration_service.py`

- 保持兼容包装职责。
- 不在该服务中复制 skill 发现或执行逻辑。
- 如需注入 SkillLoader、SandboxManager，应通过 context extras 或构造依赖完成。

### 4.9 `app/backend/agents/`

#### `preset.py`

- 为需要 skill 的 agent 定义统一 setup 约定。
- 明确 SkillTool、ReadSkillResource 和 RunSkillScript 的注册条件。
- 避免每个 preset 重复创建不同 SkillLoader。

#### `sandboxed_coder/preset.py`

Phase 7：

- 继续使用 `SandboxedBashTool` 和 `SandboxedPythonTool`。
- 按需增加 `RunSkillScript`。
- 复用同一 conversation 的 `SandboxManager`。
- 文件工具和脚本工具必须共享同一个 workspace。

### 4.10 `.agents/skills/` 和 `.agent/skills/`

Phase 4、8：

- 新 skill 默认放入 `.agents/skills/`。
- 现有 `.agent/skills/` 保持可用。
- 可将 `data-analysis` 作为完整示例，但迁移必须单独提交，避免和运行时代码混在一起。

标准目录：

```text
.agents/skills/example-analysis/
├── SKILL.md
├── scripts/
│   └── analyze.py
├── references/
│   └── format.md
└── assets/
    └── template.csv
```

### 4.11 `tests/`

#### `test_skills.py`

- `base_dir` 和三类资源发现。
- 嵌套资源、稳定排序、POSIX 路径。
- 空目录、隐藏文件、数量限制。
- 越界 symlink。
- 四级默认扫描路径和同名优先级。
- scaffold 默认目录。
- 旧字段和缓存行为兼容。

#### `test_skill_tool.py`（新增或从 `test_skills.py` 拆分）

- 结构化激活输出。
- `${ARGUMENTS}`。
- XML 特殊字符。
- 空资源。
- disabled、unknown。
- 不预读资源正文。

#### `test_skill_catalog_prompt.py`（新增）

- Catalog 内容和顺序。
- 只包含 enabled skills。
- 无 skill 时为空。
- SkillTool description 和 schema enum。

#### `test_skill_compaction.py`（新增）

- metadata 传递。
- message count trim。
- token trim。
- compaction 后保护激活对。
- 同名 skill 去重。
- 多 skill 共存。
- 持久化恢复。
- OpenAI tool-call 消息序列合法。

#### `test_skill_resource_tool.py`（新增）

- 正常读取 reference 和文本 asset。
- 未声明资源、绝对路径、`..`、越界 symlink 被拒绝。
- disabled skill 被拒绝。
- 大文件和二进制文件行为。

#### `test_skill_materializer.py`（新增）

- 物化目录结构。
- hash 复用和内容刷新。
- 只读文件。
- 不复制越界 symlink。
- conversation 隔离。

#### `test_skill_script_tool.py`（新增）

- Python 或 shell 脚本执行。
- argv 参数安全传递。
- cwd 为物化 skill 根目录。
- 脚本可读取 references/assets。
- 未声明脚本和路径逃逸被拒绝。
- timeout、输出文件和 disabled skill。
- 不调用宿主机 BashTool。

#### 现有测试

- 更新 `test_auto_compaction.py`。
- 更新 Sandbox 测试。
- 保证全部现有测试通过。

### 4.12 `docs/`

#### `skills-design.md`（新增）

- 数据契约。
- Progressive Disclosure。
- 路径优先级。
- 安全边界。
- metadata 和压缩策略。
- Sandbox 物化模型。

#### `skills.md`（新增）

- 用户如何创建 skill。
- frontmatter 和目录结构。
- scripts、references、assets 使用方式。
- enabled、激活、资源读取和脚本执行。
- 完整示例。

#### `agent-integration.md`

- 宿主应用如何注册 SkillLoader 和相关工具。
- enabled skills 如何进入 Catalog。
- SandboxManager 的依赖注入。

#### `sandbox-deployment.md`

- skill 包如何进入会话 workspace。
- 为什么不能使用宿主机 BashTool。
- workdir、网络、资源限制和清理策略。

#### `PUBLIC_API.md`

- 公开 `SkillResources` 等类型。
- 更新 SkillTool。
- 记录新增资源读取和脚本执行工具。

## 5. 第一批：标准 Skill 包和结构化激活

范围：Phase 0、Phase 1、Phase 2、Phase 4。

交付：

- Message 持久化契约（`to_persistent` / `from_persistent`，含 metadata）。
- `SqlitePersistence` 改用完整序列化。
- `SkillResources` 和 `base_dir`。
- 完整资源枚举（`scripts/`、`references/`、`assets/`）。
- `Skill.activation_content()` 结构化输出。
- `SkillTool` 返回 XML 激活内容。
- `.agents/skills/` 路径兼容，保留 `.agent/skills/`。
- frontmatter `name` 与目录名不一致时的 warn 策略。
- 对应单元测试和基础文档。

完成条件：

- 不执行脚本也能完整表达标准 skill 包。
- 模型激活 skill 后能看到正文和资源清单。
- 现有 SkillTool 调用和测试不回归。
- 持久化层能保存和恢复 message metadata。

## 6. 第二批：Enabled Skill Catalog

范围：Phase 3。

交付：

- `SkillCatalogPrompt`。
- `SkillTool.description` + schema enum 动态 Catalog。
- 可选的 system prompt Catalog 注入（`RunConfig.enable_skill_catalog` / `AgentDefinition.enable_skill_catalog`）。
- 防止 `SkillTool` schema 跨会话污染。

完成条件：

- 模型能够知道有哪些 enabled skills。
- 禁用 skill 不出现在 Catalog 和 enum 中。
- system prompt 和 tool description 两种注入方式不重复。

## 7. 第三批：上下文保护

范围：Phase 5。

交付：

- Skill 激活 metadata（`ToolOutput.metadata` 经执行器进入 `Message.tool()`）。
- `Memory._trim()` 保护 skill 激活消息对。
- `LLMSummaryCompactor` 提取 protected pairs 后压缩。
- 持久化层完整保存/恢复 metadata。

完成条件：

- 长对话压缩后 skill 指令不丢失。
- 重启并加载持久化消息后，skill 保护仍然有效。
- 压缩后 OpenAI tool-call 序列合法。

## 8. 第四批：Skill 资源安全读取

范围：Phase 6。

交付：

- `ReadSkillResource` 工具。
- `src/agentengine/skills/paths.py` 路径校验。
- 文本资源大小限制和二进制 asset 元信息返回。

完成条件：

- 模型能够按需读取声明资源。
- 无法通过资源工具读取 skill 根目录外文件。

## 9. 第五批：Sandbox 脚本执行

范围：Phase 7。

交付：

- `SkillMaterializer`
- `RunSkillScript`
- Sandbox `exec_argv()` 或等价安全入口
- 受控 workdir
- conversation 隔离和清理

完成条件：

- skill 脚本只在 Sandbox 中运行。
- 脚本可读取包内资源并输出到会话 workspace。
- 参数、路径、symlink、timeout 和资源限制测试通过。
- Sandbox 不可用时明确拒绝，不回退宿主机执行。

## 10. 提交拆分

1. `feat(memory): add persistent message serialization with metadata`
2. `feat(persistence): persist full message payload including metadata`
3. `feat(skills): discover packaged skill resources`
4. `feat(skills): return structured activation content`
5. `feat(skills): support standard skill roots`
6. `feat(skills): expose enabled skill catalog via tool description and optional system prompt`
7. `feat(memory): preserve activated skills during trim and compaction`
8. `feat(skills): add secure resource reader`
9. `feat(sandbox): materialize and execute skill scripts`
10. `docs(skills): document packaged skills and execution`

提交顺序说明：
- 提交 1-2 属于 Phase 0，必须在 Phase 5 之前完成，否则 metadata 保护重启后失效。
- 提交 3-5 属于第一批，可独立发布。
- 提交 6 属于第二批，依赖提交 3-5。
- 提交 7 属于第三批，依赖提交 1-2。
- 提交 8 属于第四批，依赖提交 3-5。
- 提交 9 属于第五批，依赖提交 3-8。

## 11. 实施契约修订

本节修正评审中发现的六项实现问题，是后续开发、测试和代码评审的强制约束。

### 11.1 工具返回值与执行结果分层

当前 `ToolExecutor` 调用 `tool.run()` 后自行创建 `ToolExecutionResult`。因此，工具不得直接返回
`ToolExecutionResult`，否则执行器会把它当作普通 raw value 再次包装，metadata 无法可靠传递。

在 `src/agentengine/tools/base.py` 新增独立业务返回类型：

```python
@dataclass(slots=True)
class ToolOutput:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
```

实现规则：

- 普通工具仍可返回 `str`、dict 或其他兼容值。
- 需要附加 metadata 的工具返回 `ToolOutput`。
- `SkillTool.run()` 返回 `ToolOutput(content=..., metadata=...)`。
- `ToolExecutor` 识别并解包 `ToolOutput`，再生成唯一一层 `ToolExecutionResult`。
- `ToolExecutionResult` 保留 `metadata` 字段，供 runtime 写入 `Message.tool()`。
- streaming tool 的 final data 同样允许为 `ToolOutput`。
- summarization 和 truncation 只处理 `ToolOutput.content`，不能丢失 metadata。

对应文件：

```text
src/agentengine/tools/base.py
src/agentengine/tools/executor.py
src/agentengine/tools/builtin/skill_tool.py
src/agentengine/runtime/turn.py
tests/test_tool_executor.py
tests/test_skill_tool.py
```

### 11.2 Catalog 注入时机统一

真实生命周期为：

```text
AgentEngine.run()
  -> TurnRunner.run()
  -> run_turn()
  -> agent.setup()
  -> preset setup 注册最终工具
  -> memory.load_from_db()
  -> LLM loop
```

因此不能在 `AgentEngine.run()` 的普通入口中假设 preset setup 已完成。

最终方案：

- 默认方案仍是动态 `SkillTool.description + schema enum`，不修改 system prompt。
- 可选 system Catalog 的注入点位于 `run_turn()`：
  - `await agent.setup()` 之后；
  - `memory.load_from_db()` 之前；
  - 首次 LLM 调用之前。
- `TurnRunner` 不负责组装 Catalog。
- `AgentEngine` 只负责提供配置，不直接读取最终 ToolCollection。
- system Catalog 以带 metadata 的 system message 注入：

```python
{
    "skill_catalog": True,
    "generated": True,
}
```

- 每次 run 注入前先删除旧的 generated Catalog，避免会话持久化后重复累积。
- 当 system Catalog 开启时，`SkillTool.description` 只保留简短调用说明和 schema enum，不重复完整 Catalog。

配置建议放在 `RunConfig` 或 `AgentDefinition`：

```python
enable_skill_catalog: bool = False
```

不要只在 `AgentEngine.__init__()` 增加一个无法被单个 agent 覆盖的全局开关。

### 11.3 持久化消息契约升级

`Memory.save_to_db()` 当前使用 `to_openai()`，`load_from_db()` 当前使用 `from_openai()`。
为了保存 metadata，需要将“模型传输格式”和“持久化格式”彻底分离。

新增：

```python
Message.to_persistent()
Message.from_persistent()
```

持久化 payload：

```json
{
  "_format": "agentengine.message.v1",
  "role": "tool",
  "content": "...",
  "tool_call_id": "...",
  "tool_calls": null,
  "metadata": {
    "skill_activation": true,
    "skill_name": "data-analysis",
    "protected": true
  }
}
```

约束：

- `Message.to_openai()` 永远不输出内部 metadata。
- `Memory.save_to_db()` 使用 `to_persistent()`。
- `Memory.load_from_db()` 使用 `from_persistent()`。
- 无 `_format` 的旧 payload 按现有 OpenAI 消息格式兼容解析。
- 更新 `PersistencePort` 文档：messages payload 是 AgentEngine 持久化格式，不再等同于 OpenAI API 格式。
- 更新 SQLite、fake、mock 和所有第三方 Persistence 契约测试。
- Web API 对外返回消息时过滤 `_format` 和内部 metadata。
- 默认不把完整 `base64_image` 写入 SQLite；应保存文件或对象存储引用，避免 payload 无限制膨胀。

对应文件：

```text
src/agentengine/memory/message.py
src/agentengine/memory/memory.py
src/agentengine/persistence/port.py
src/agentengine/persistence/sqlite.py
app/backend/services/web_api.py
tests/test_memory.py
tests/test_persistence_sqlite.py
```

### 11.4 frontmatter 严格与兼容模式

为了同时满足标准实现和旧目录兼容：

- `.agents/skills/` 默认使用严格模式。
- `.agent/skills/` 默认使用兼容模式。
- 严格模式下，frontmatter `name` 不合法或与目录名不一致时跳过该 skill，并记录明确错误。
- 兼容模式下，记录 warning 后继续使用现有行为。
- 显式传入 `roots` 时允许调用方通过 `strict_validation` 指定行为。
- `scaffold()` 生成的目录名和 frontmatter `name` 必须一致。

验收测试必须分别覆盖严格和兼容模式，文档不得再将“仅 warning 后继续加载”描述为统一规则。

### 11.5 protected 消息原子追加

当前 memory 在每次 `append()` 后立即执行 `_trim()`。assistant tool call 先写入、tool result 后写入，
如果分别裁剪，可能在调用对完整之前删除 assistant message。

新增以下任一等价能力，推荐第一种：

```python
Memory.extend_atomic(messages: Iterable[Message])
```

实现要求：

- tool execution 完成后，将 assistant tool call 和对应 tool result 作为一个逻辑组处理。
- 组内消息不能被拆分保留。
- `_trim()` 和 Compactor 使用 `tool_call_id` 建立调用对。
- Skill 激活组携带稳定 group metadata，例如：

```python
{
    "message_group": "skill_activation:<tool_call_id>",
    "skill_name": "data-analysis",
    "protected": True,
}
```

- 同名 skill 重复激活时，只保留最新完整组。
- 若 protected groups 加 system messages 已超过 token budget，应抛出明确的上下文错误，不得静默删除 skill。
- 普通工具调用也不得生成孤立 tool result；受保护 skill 只是具有额外保留优先级。

需要同步修改 `runtime/turn.py` 的工具消息写入时机，而不仅是修改 `_trim()`。

### 11.6 资源数量上限必须失败

资源枚举上限默认 100 个文件，可配置，但不得静默截断。

超限行为：

- `SkillLoader` 将该 skill 标记为 discovery error，不放入可激活 skill 集合。
- 日志和管理 API 返回 skill 名称、实际计数、限制值和目录。
- Catalog、schema enum 和 enabled names 中不得出现加载失败的 skill。
- `SkillTool` 对该名称返回明确的 unavailable 诊断。
- 后续可以增加分页资源清单，但在实现分页前不能通过截断伪装为完整 skill。

测试覆盖：

- 刚好达到上限时成功。
- 超过上限一个文件时失败。
- 失败 skill 不进入 Catalog 和 schema enum。
- 错误信息不泄露无关宿主机路径。

## 12. 修订后的提交拆分

1. `feat(memory): separate persistent message serialization from llm payloads`
2. `feat(persistence): persist versioned message payloads with metadata`
3. `feat(tools): add metadata-aware tool output contract`
4. `feat(skills): discover validated packaged skill resources`
5. `feat(skills): return structured activation content`
6. `feat(skills): support strict and compatible skill roots`
7. `feat(skills): expose enabled skill catalog after agent setup`
8. `feat(memory): preserve atomic skill activation groups`
9. `feat(skills): add secure resource reader`
10. `feat(sandbox): materialize and execute skill scripts`
11. `docs(skills): document packaged skills and execution`

依赖顺序：

- 提交 1-3 是上下文保护的基础契约，必须先完成。
- 提交 4-6 完成标准 skill 包发现和兼容路径。
- 提交 7 依赖最终 ToolCollection，注入逻辑位于 `run_turn()` setup 之后。
- 提交 8 依赖提交 1-3。
- 提交 9 依赖提交 4-6。
- 提交 10 依赖提交 4-6、9 和 Sandbox 基础设施。

## 13. 整体完成定义

- 完整发现 `scripts/`、`references/`、`assets/`。
- Catalog 只暴露 enabled skills。
- 激活结果包含正文、根目录和资源清单。
- references/assets 可以按需安全读取。
- scripts 只能通过 Sandbox 执行。
- 所有资源路径通过绝对路径、`..` 和 symlink 越界检查。
- trim、compaction 和持久化不会丢失激活状态。
- `.agents/skills/` 与 `.agent/skills/` 均可使用。
- 旧 `SkillTool` 调用方式保持兼容。
- `Message.metadata` 能被持久化完整保存和恢复。
- `SkillTool` schema 是 per-conversation 动态生成，不跨会话污染。
- 单元测试、集成测试和端到端场景全部通过。

## 14. 最小必要前置改动

在启动第一批前，建议先确认或完成以下改动，避免后续返工：

1. **持久化格式分层**：新增 `to_persistent()` / `from_persistent()`，改造 `PersistencePort` 与 SQLite，并加入 payload 版本。
2. **工具返回格式分层**：新增 `ToolOutput`，由 `ToolExecutor` 解包后生成 `ToolExecutionResult`。
3. **消息原子追加**：assistant tool call 与对应 tool result 作为完整组追加、裁剪和压缩。
4. **动态 Tool schema**：`SkillTool.to_openai_tool()` 按当前 enabled skills 生成 description 和 enum，不修改类级 schema。
5. **Catalog 生命周期**：可选 system Catalog 只在 `run_turn()` 的 `agent.setup()` 后注入。
6. **Loader 失败语义**：严格/兼容名称校验明确，资源数量超限时 skill discovery 失败。
