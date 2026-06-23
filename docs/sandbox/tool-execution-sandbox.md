# AgentEngine 工具执行与沙盒完善计划

## 背景

当前 AgentEngine 的工具执行链路存在两个核心问题：

1. **`read_file` 经常返回 error**：路径解析过于严格，绝对路径（如 `/workspace/...`）被 workspace root 检查拒绝。
2. **沙盒未默认启用**：`BashTool` 在宿主机直接 `create_subprocess_exec`，无任何隔离。`SandboxedBashTool` / `SandboxedPythonTool` 虽已完整实现，但仅 `sandboxed_coder` preset 可选使用，web_api 从未注入。

同时参考了 **Claude Code** 和 **OpenAI Codex CLI** 的沙盒规范，两者已形成事实上的行业标准——本计划确保 AgentEngine 与之一致。

---

## 与 Claude Code / Codex CLI 的规范对比

### 三者都会收敛到的标准模型

```
┌─────────────────────────────────────────────────┐
│                   Agent Loop                     │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ 文件工具  │  │ 只读工具  │  │ Shell 工具    │  │
│  │ read_file │  │ grep     │  │ bash / python │  │
│  │ write_file│  │ glob     │  │               │  │
│  │ edit_file │  │ Skill    │  │               │  │
│  └────┬──────┘  └────┬─────┘  └──────┬────────┘  │
│       │              │               │           │
│       │    宿主机执行  │               │  沙盒执行  │
│       │    (权限系统)  │               │  (OS 隔离) │
│       ▼              ▼               ▼           │
│  ┌──────────────────────────────────────────┐    │
│  │        共享 Workspace (bind mount)        │    │
│  │    host: /var/agent/sessions/{conv_id}   │    │
│  │    sandbox: /workspace                   │    │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │           ExecPolicy (工具防火墙)         │    │
│  │     白名单/黑名单/审批 → 每次工具调用前    │    │
│  └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

### Claude Code 规范

| 维度 | Claude Code 做法 |
|---|---|
| **沙盒机制** | macOS: Seatbelt; Linux/WSL2: bubblewrap + seccomp；**非 Docker** |
| **沙盒范围** | 仅 Bash 命令及子进程；文件工具不经过沙盒 |
| **默认状态** | **关闭**（`/sandbox` 命令手动开启） |
| **网络隔离** | 应用层代理（socat），按域白名单 |
| **Workspace** | 项目根目录 = 沙盒工作目录，文件工具和 bash 共享 |
| **安全边界** | 权限系统（应用层）+ 沙盒（OS 层），纵深防御 |
| **绕过机制** | `dangerouslyDisableSandbox`，可被企业策略锁定 |

### Codex CLI 规范

| 维度 | Codex CLI 做法 |
|---|---|
| **沙盒机制** | macOS: Seatbelt; Linux: bubblewrap + seccomp + Landlock；**非 Docker** |
| **沙盒范围** | 仅 Shell 命令；文件工具不经过沙盒 |
| **默认状态** | **开启**（`workspace-write` 模式）；拒绝执行若策略不可强制 |
| **网络隔离** | 内核级 seccomp BPF 过滤 `socket()` 系统调用 |
| **Workspace** | 项目目录可写，`.git`/`.codex`/`.agents` 强制只读 |
| **安全边界** | 内核级强制访问控制，deny 规则永远优先生效 |
| **权限模型** | Profile 系统（`:read-only` / `:workspace` / `:danger-full-access`） |

### AgentEngine 当前状态 vs 目标

| 维度 | 当前 AgentEngine | 目标（对齐 Claude Code / Codex） |
|---|---|---|
| **沙盒机制** | Docker 容器（已实现未启用） | Docker 容器（服务端场景更合适） |
| **沙盒范围** | bash + python（已实现） | bash + python ✅ |
| **默认状态** | **未启用** | **默认启用**（偏向 Codex 的 secure-by-default） |
| **文件工具隔离** | 无沙盒（和标准一致） | 宿主机执行 ✅ |
| **Workspace 统一** | 未统一（read_file 和 bash workspace 不同） | 统一共享，自动对齐 |
| **ExecPolicy** | 已实现但未默认配置 | 默认策略 + 可自定义 |
| **绕过控制** | 无 | 参考 Claude Code 的 `allowUnsandboxedCommands` |

### 关键差异说明：为什么 AgentEngine 用 Docker 而非 bubblewrap

Claude Code 和 Codex CLI 是**桌面 CLI 工具**，可以直接调用 OS 原生沙盒。AgentEngine 是**服务端 Python SDK**：

- bubblewrap/Seatbelt 需要特定 OS + 内核版本 + 二进制依赖，在服务端容器化部署中不可靠
- Docker 是服务端最通用的隔离方式，跨平台一致
- AgentEngine 的 Docker 沙盒实现了**同等安全等级**（no_network、cap_drop ALL、non-root user、no_new_privileges、cgroup 限制）

**这不是规范偏离，而是部署场景的适配。** 安全模型保持一致：文件工具在宿主机、Shell 在隔离环境、共享 workspace。

---

## 改造阶段

### Phase 1：修复 read_file 路径解析（低风险，立即见效）

**问题**：
- 模型拿到沙盒内的绝对路径（如 `/workspace/output.txt`）调 `read_file`，被 `resolved.relative_to(self._root)` 拒绝
- workspace_root 不统一：web_api 用 `REPO_ROOT`，agent setup 用 `cwd`

**改动文件**：`src/agentengine/tools/builtin/read_file_tool.py`

**改动内容**：

1. `ReadFileTool` 接受多个 workspace root：`workspace_roots: list[Path]`
2. 路径解析逻辑改为：对每个 root 尝试 resolve，第一个成功且存在的即接受
3. 绝对路径先检查是否真实存在，再判断是否在任一 root 下
4. 向后兼容：`workspace_root` 参数保留，内部转成单元素 list

```python
class ReadFileTool(Tool):
    def __init__(self, workspace_root=None, *, extra_roots=None):
        self._roots = [_norm(root) for root in ([workspace_root] + (extra_roots or []))]
    
    async def run(self, **kwargs):
        # 1. 绝对路径 + 真实存在 → 直接读取（安全：只读工具）
        # 2. 相对路径 → 在每个 root 下尝试
        # 3. 都不匹配 → 返回友好错误
```

**验收**：
- 模型传 `/workspace/output.txt`（沙盒内路径）能正常读取
- 现有测试不挂

---

### Phase 2：SandboxManager 默认注入（核心）

**问题**：`SandboxManager` 存在但从未被 web_api 创建和注入，导致所有 agent 无沙盒执行能力。

**改动文件**：
- `app/backend/services/web_api.py`
- `src/agentengine/engine.py`（可选增强）

**改动内容**：

1. **web_api 启动时创建 `SANDBOX_MANAGER` 单例**：
```python
SANDBOX_MANAGER = SandboxManager(
    sessions_root=REPO_ROOT / "data" / "sandbox-sessions",
    config=SandboxConfig(),  # 默认安全配置
)
```

2. **在 `_run_agent_events` 中自动注入**：
```python
context.extras["sandbox_manager"] = SANDBOX_MANAGER
context.extras["workspace_root"] = str(
    SANDBOX_MANAGER.host_workspace_for(scoped_conversation_id)
)
```

3. **`ReadFileTool` 使用注入的 workspace_root**（衔接 Phase 1）：
```python
ws_root = context.extras.get("workspace_root", REPO_ROOT)
context.tool_collection.add(ReadFileTool(workspace_root=ws_root))
```

4. **会话结束时释放沙盒**（在 finally 块中）：
```python
SANDBOX_MANAGER.release(scoped_conversation_id)
```

5. **应用 shutdown 时清理**：
```python
@app.on_event("shutdown")
async def shutdown():
    SANDBOX_MANAGER.shutdown()
```

**验收**：
- 每个 conversation 自动获得独立沙盒容器
- 容器在会话结束后正确释放
- 无 Docker 时优雅降级（fallback 到 host BashTool + 警告日志）

---

### Phase 3：默认启用 SandboxedBashTool / SandboxedPythonTool

**问题**：当前 preset 的 `tools: [bash]` 解析为宿主 `BashTool`，不是 `SandboxedBashTool`。

**改动文件**：
- `src/agentengine/tools/builtin/__init__.py`
- `src/agentengine/definition_loader.py`
- `app/backend/agents/markdown/` 下的 preset 文件

**方案：在 `build_default_tools_for_context` 中自动检测 sandbox**

```python
def build_default_tools_for_context(context, *, include=None, exclude=None):
    manager = context.extras.get("sandbox_manager")
    conv_id = context.conversation_id
    
    for name in names:
        if name == "bash" and manager and conv_id:
            # 有 sandbox → 用 SandboxedBashTool
            tools.append(SandboxedBashTool(manager=manager, conversation_id=conv_id))
        elif name == "python" and manager and conv_id:
            tools.append(SandboxedPythonTool(manager=manager, conversation_id=conv_id))
        else:
            tools.append(factory(workspace_root, ...))
```

**Fallback 策略**（参考 Claude Code 的 `dangerouslyDisableSandbox`）：
- 有 Docker + SandboxManager → `SandboxedBashTool`（沙盒执行）
- 无 Docker → `BashTool`（宿主机执行）+ WARNING 日志
- 通过 `AgentEngine(..., require_sandbox=True)` 强制要求沙盒

**验收**：
- 有 Docker 环境：bash/python 在容器内执行
- 无 Docker 环境：自动降级到宿主机执行 + 日志警告
- `require_sandbox=True` 时：无 Docker 则拒绝执行

---

### Phase 4：默认 ExecPolicy 安全策略

**问题**：`ExecPolicy` 已实现但从未默认配置，所有命令均可执行。

**改动文件**：
- 新增 `src/agentengine/tools/policy_presets.py`

**改动内容**：

```python
# 参考 Claude Code 和 Codex CLI 的默认安全策略
SANDBOX_DEFAULT_POLICY = ExecPolicy(
    rules=[
        # 阻止明显的破坏性命令（对齐 Claude Code 的 destructive 检测）
        ExecPolicyRule("rm -rf /", ExecPolicyAction.DENY, "拒绝递归删除根目录"),
        ExecPolicyRule("mkfs.", ExecPolicyAction.DENY, "拒绝格式化文件系统"),
        ExecPolicyRule("dd if=", ExecPolicyAction.DENY, "拒绝裸磁盘写入"),
        ExecPolicyRule(":(){ :|:& };:", ExecPolicyAction.DENY, "拒绝 fork bomb"),
        ExecPolicyRule("shutdown", ExecPolicyAction.DENY, "拒绝关机"),
        ExecPolicyRule("reboot", ExecPolicyAction.DENY, "拒绝重启"),
        # 高危 git 操作需要审批
        ExecPolicyRule("git push --force", ExecPolicyAction.ASK, "强制推送需要确认"),
        ExecPolicyRule("git reset --hard", ExecPolicyAction.ASK, "硬重置需要确认"),
    ],
    default=ExecPolicyAction.ALLOW,  # 其余允许（沙盒已是安全边界）
)
```

**注入方式**：在 `_run_agent_events` 中自动注入到 `context.extras["exec_policy"]`。

**验收**：
- 危险命令被拒绝或需审批
- 沙盒内正常命令不受影响

---

### Phase 5：上下文保护（skill content 不被压缩丢失）

**问题**：当 skill 被激活后，其指令可能在长对话中被 `LLMSummaryCompactor` 压缩掉。

**改动文件**：
- `src/agentengine/memory/message.py`（如需要）
- `src/agentengine/runtime/compaction.py`

**改动内容**：

1. Skill 激活时，在 `Message.metadata` 中标记 `skill_name` 和 `protected: True`
2. `LLMSummaryCompactor.compact()` 检查消息的 metadata，受保护消息不进入摘要
3. 替代方案（当前可用）：确保 Skill 消息是 system 消息（system 消息默认不被压缩）

```python
# compaction.py 中的改动
def _should_protect(message: Message) -> bool:
    return message.metadata.get("protected", False)

async def compact(self, messages):
    old = [m for m in messages if not _should_protect(m)]
    protected = [m for m in messages if _should_protect(m)]
    summary = await self._summarize(old)
    return [system_msg, summary, *protected, *recent]
```

**验收**：
- 长对话中 skill 激活后的指令不被压缩
- 普通消息正常压缩

---

### Phase 6：测试与文档

**新增/修改测试**：
- `tests/test_read_file_paths.py`：多 root、绝对路径、沙盒路径
- `tests/test_sandbox_integration.py`：Engine + SandboxManager 集成
- `tests/test_sandbox_fallback.py`：无 Docker 时降级行为
- `tests/test_exec_policy_default.py`：默认策略规则

**文档更新**：
- `PUBLIC_API.md`：更新沙盒相关 API
- `docs/tool-execution-sandbox/`：本计划 + 部署指南
- `docs/sandbox-deployment.md`：补充默认启用说明

---

## 执行顺序

| 顺序 | Phase | 价值 | 风险 | 依赖 |
|---|---|---|---|---|
| 1 | Phase 1: 修复 read_file | 立即修复用户可见 bug | 低 | 无 |
| 2 | Phase 2: SandboxManager 注入 | 沙盒基础设施可用 | 中 | 需要 Docker |
| 3 | Phase 3: 默认沙盒工具 | bash/python 隔离执行 | 中 | Phase 2 |
| 4 | Phase 4: 默认 ExecPolicy | 纵深防御 | 低 | 无 |
| 5 | Phase 5: 上下文保护 | 长对话稳定性 | 低 | 无 |
| 6 | Phase 6: 测试文档 | 质量保证 | 低 | Phase 1-5 |

**推荐先做 Phase 1 + Phase 2**：修 bug + 让沙盒可工作，价值最高。

---

## 安全模型总结

```
Layer 1: ExecPolicy (应用层防火墙)
  └─ 每次工具调用前检查：白名单/黑名单/审批
  └─ 阻止已知危险模式 (rm -rf /, mkfs, fork bomb, ...)

Layer 2: Sandbox Container (OS 级隔离)
  └─ 无网络 (network_disabled=True)
  └─ 无 Linux capabilities (cap_drop=ALL)
  └─ 非 root 用户 (user=sandbox)
  └─ 禁止提权 (no_new_privileges=True)
  └─ 资源限制 (memory=256m, CPU=0.5, pids=64)
  └─ 只写 workspace（bind mount），其余只读

Layer 3: Workspace 隔离
  └─ 文件工具 (read_file, write_file) → 宿主执行，workspace root 限制
  └─ Shell 工具 (bash, python) → 容器执行，bind mount 共享 workspace
  └─ 会话隔离：每 conversation 独立 workspace + 独立容器
```

这与 **Claude Code** 的"权限系统 + 沙盒"和 **Codex CLI** 的"内核级强制访问 + profile 系统"在架构上等价。差异仅在实现机制（Docker vs bubblewrap），这是服务端 vs 桌面的合理分化。
