# 代码沙盒：设计与部署指南

在隔离环境中运行不可信代码（LLM 生成的代码或用户提交的代码）的方案文档，涵盖架构落点、
集成方式与部署步骤。

**配套代码**

| 路径 | 内容 |
|---|---|
| `src/agentengine/sandbox/` | 核心实现（配置、会话沙盒、生命周期管理、工具适配） |
| `deploy/sandbox/Dockerfile.session` | 会话沙盒镜像 |
| `app/backend/agents/sandboxed_coder/` | 接入示例 preset |
| `tests/test_sandbox.py` | 单元测试（使用 fake Docker client，不依赖真实容器） |

---

## 目录

1. [适用范围](#1-适用范围)
2. [前置条件](#2-前置条件)
3. [架构落点](#3-架构落点)
4. [执行引擎：会话容器](#4-执行引擎会话容器)
5. [隔离模型](#5-隔离模型)
6. [文件传递](#6-文件传递)
7. [集成步骤](#7-集成步骤)
8. [资源与容量](#8-资源与容量)
9. [延迟特征](#9-延迟特征)
10. [安全约束](#10-安全约束)
11. [可选：升级隔离强度](#11-可选升级隔离强度)
12. [本地验证](#12-本地验证)
13. [附录 A：配置项参考](#附录-a配置项参考)

---

## 1. 适用范围

**目标**：在受控主机上执行不可信代码，确保其无法读取主机敏感数据、无法越权访问网络、
无法耗尽主机资源。

**隔离强度**：本方案采用 Docker（runc）容器加权限收紧，属于入门档。可平滑升级到 gVisor
（见 [第 11 节](#11-可选升级隔离强度)）；microVM（Firecracker/Kata）与集群编排（Kubernetes
RuntimeClass）不在本文范围。

**非目标**：本文不涉及对象存储集成、多副本部署、跨主机调度。单进程、单主机为基准；
多副本所需的分布式锁、共享存储等由调用方另行实现。

---

## 2. 前置条件

| 依赖 | 要求 | 说明 |
|---|---|---|
| Docker Engine | 已安装并运行 | 提供容器隔离与回收 |
| Python | ≥ 3.11 | 与 AgentEngine 一致 |
| Docker SDK | `docker>=7.1.0` | 通过可选依赖 `agentengine[sandbox]` 安装 |
| 主机权限 | Agent App 可访问 Docker daemon | 安全注意事项见 [第 10 节](#10-安全约束) |

```bash
pip install -e ".[sandbox]"        # 或 uv sync --extra sandbox
```

> 本文命令以 Debian 系发行版为例。其它发行版请替换对应的包管理器与 Docker 安装方式。

---

## 3. 架构落点

沙盒接入点位于 **`Tool` 抽象层**。新增的 `SandboxedBashTool` / `SandboxedPythonTool` 与
内置 `BashTool` 同名（`"bash"` / `"python"`），仅将 `run()` 的执行体从「主机 subprocess」
替换为「`exec` 进会话容器」。其上的 `ToolExecutor`、`ExecPolicy`、OpenAI tool schema、
SSE 事件流均保持不变。

```
TurnRunner / ToolExecutor      可信，运行于主机，不修改
        │  仅依赖 Tool.run() 抽象
        ▼
SandboxedBashTool ("bash")     新增：实现 Tool 接口
        │  manager.acquire(conversation_id)
        ▼
SandboxManager                 新增：与 ConversationLockManager 同级（按 conversation 索引）
        │  Docker SDK
        ▼
会话容器                        每个 conversation 一个，结束后销毁
```

设计要点：执行单元与控制端（Agent App）之间隔着一条信任边界。控制端可信、运行于主机；
不可信代码运行于容器内。隔离能力由容器 runtime 提供，与编排、调度正交——本方案不依赖
Kubernetes，隔离作为部署期可插拔的实现接入。

---

## 4. 执行引擎：会话容器

执行引擎为 `SessionSandbox`：容器在会话期间常驻（`sleep infinity`），每次执行 `exec`
进入容器。

| 特性 | 行为 |
|---|---|
| 容器创建开销 | 300ms–1s，**每个会话仅付一次**（容器常驻） |
| 单次执行延迟 | 50–200ms |
| 文件 / 已装包 | 在会话内保留 |
| 内存变量 | **不保留**（每次 `python -c` 为全新进程） |
| 富输出 | 写入文件，由主机收集 |

内存变量不驻留是有意设计：每次执行为独立进程，天然不受上一次执行的状态影响，从根本上
规避了「会话内状态污染」。图表、表格等产物通过写文件方式输出（例如
`plt.savefig('/workspace/chart.png')`），由主机收集后转存对象存储，与「输出文件」型
产品形态契合。

---

## 5. 隔离模型

### 5.1 隔离单位为「会话」而非「用户」

`SandboxManager` 以 `conversation_id` 为索引，与 `ConversationLockManager` 的颗粒度一致。
同一用户的 N 个会话对应 N 个独立容器，彼此不可见对方的变量、文件与资源。

### 5.2 防污染

- **会话之间**：物理隔离于不同容器，状态无法互串。约束为**不复用容器、会话结束即
  `release` 销毁并擦除 workspace**（`SandboxManager.release` 已实现 `shutil.rmtree`）。
- **会话之内**：每次 `python -c` 为全新进程，内存变量本就不驻留，不存在「上一次执行污染
  下一次」的情况；以 `mem_limit` 作为内存兜底（超限触发 cgroup OOM）。

---

## 6. 文件传递

每个会话对应一个主机目录，bind mount 进容器 `cfg.workspace_mount`（默认 `/workspace`）。
该通道走文件系统而非网络，因此 `network_disabled=True` 下仍然成立，双向可达。

```python
host_ws = manager.host_workspace_for(conversation_id)   # 主机侧目录
# 传入：主机写入 host_ws / "data.csv"   → 容器内 /workspace/data.csv 可读
# 传出：容器内写 /workspace/result.xlsx → 主机 host_ws / "result.xlsx" 可取
```

将文件工具的 workspace 根指向同一目录，使 `bash` / `python` 与 `read_file` / `write_file`
共享同一份文件系统：

```python
context.extras["workspace_root"] = str(manager.host_workspace_for(conversation_id))
```

---

## 7. 集成步骤

### 7.1 准备镜像

```bash
# 在仓库根目录执行
docker build -f deploy/sandbox/Dockerfile.session -t agentengine-sandbox:session .
```

验证权限收紧已生效（无网络访问应当失败）：

```bash
docker run --rm --network none agentengine-sandbox:session \
  python -c "import urllib.request as u; u.urlopen('http://example.com')"
# 期望抛出 URLError，表明 network off 生效
```

### 7.2 构造进程级单例

参照服务中 `PERSISTENCE` / `LOCK_MANAGER` 等进程级单例，新增 `SANDBOX_MANAGER`：

```python
from agentengine.sandbox import SandboxManager, SandboxConfig

SANDBOX_MANAGER = SandboxManager(
    sessions_root="/var/agent/sessions",       # 示例路径，按部署调整
    config=SandboxConfig(
        image="agentengine-sandbox:session",
        mem_limit="512m",
        nano_cpus=2_000_000_000,               # 2 核
        max_containers=24,                     # 并发上限，依内存预算设定，见第 8 节
        idle_ttl_seconds=900,                  # 空闲回收阈值（秒）
        # runtime="runsc",                     # 升级 gVisor 时启用，见第 11 节
    ),
)
```

`sessions_root` 需为 Agent App 进程可读写的目录。

### 7.3 注入 context

在请求处理流程中，将 manager 与 workspace 注入 context：

```python
context.extras["sandbox_manager"] = SANDBOX_MANAGER
context.extras["workspace_root"] = str(
    SANDBOX_MANAGER.host_workspace_for(scoped_conversation_id)
)
```

### 7.4 释放容器

会话结束时销毁容器并擦除 workspace，置于运行收尾的 `finally` 中：

```python
    finally:
        SANDBOX_MANAGER.release(scoped_conversation_id)   # 销毁容器 + 擦除 workspace
        await event_stream.close()
```

> 若产品为多轮对话且需要容器跨请求保留，则不应在每次请求后 `release`，而应在会话真正结束
> （用户关闭或超时）时释放，并以 `idle_ttl_seconds` 作为兜底回收。

应用关闭时清场：

```python
@app.on_event("shutdown")
async def _cleanup():
    SANDBOX_MANAGER.shutdown()
```

### 7.5 preset 接入

在 preset 的 `setup` 钩子中，以沙盒工具替代内置 `BashTool`，参见
`app/backend/agents/sandboxed_coder/preset.py`。由于工具同名，对 `ToolExecutor` 与模型透明。

---

## 8. 资源与容量

单容器内存上限由 `mem_limit` 决定，最坏情况主机沙盒内存约为：

```
最坏内存 ≈ max_containers × mem_limit
```

据此按内存预算反推并发上限（以 `mem_limit=512m` 为例）：

| 可用内存预算 | 建议 `max_containers` | 沙盒最坏占用 |
|---|---|---|
| 8 GB | 12 | ≈ 6 GB |
| 16 GB | 24 | ≈ 12 GB |
| 32 GB | 48 | ≈ 24 GB |

> 表中为示例换算，需为主机系统、Agent App、LLM 客户端预留余量，不应让沙盒占满全部内存。

CPU 方面，`nano_cpus` 为单容器上限而非预留（`2_000_000_000` = 2 核），依赖 cgroup 时间片
共享，可适度超卖。

两道闸门已内置：

- `max_containers`：超限时 `acquire` 抛出 `SandboxCapacityError`，上层可排队或降级。
- `idle_ttl_seconds`：`acquire` 时顺带 `reap_idle`，回收空闲超时的会话容器。

---

## 9. 延迟特征

数量级参考，实际取决于主机、镜像与存储驱动。

| 执行方式 | 端到端额外开销 |
|---|---|
| 一次性容器（每次创建并销毁） | 300ms–1s（无状态，最慢） |
| **会话容器（本方案）** | 首次 300ms–1s；之后 50–200ms / 次 |
| 预热池（预建空容器，按需领取） | 近乎为零，仅余执行本身 |

如需进一步压低开会话的首次延迟，可引入预热池：后台预建一批空容器，请求到达时直接领取。
注意预热池领取的容器服务完一个会话后仍需销毁补新，不可擦除后复用。

---

## 10. 安全约束

1. **控制端运行于主机**：Agent App 为可信端，应直接运行于主机或独立的有权限调度代理。
   不应将 Agent App 容器化后再挂载 `/var/run/docker.sock`——持有该 socket 约等于持有主机
   root 权限，该容器一旦被攻破，主机即沦陷。
2. **加固参数为底线**：不可信代码进入容器时，`SandboxConfig` 默认已开启 network off、
   cap drop ALL、non-root、no-new-privileges、cgroup 限额、pids 限额。修改前需评估影响。
3. **结束即销毁**：会话结束后销毁容器、擦除 workspace、不复用（已实现）。预热池领取的
   容器服务完一个会话后同样需销毁补新。
4. **`ExecPolicy` 非安全边界**：`ExecPolicy`（字符串前缀匹配）属于应用层软约束，不可作为
   安全边界使用。真正的隔离边界是容器。

---

## 11. 可选：升级隔离强度

切换到 gVisor（runsc）可在几乎无缝的前提下显著提高逃逸难度。

```bash
# 安装 gVisor 并注册 runsc runtime，详见 https://gvisor.dev/docs/user_guide/install/
sudo runsc install && sudo systemctl restart docker
docker run --rm --runtime=runsc hello-world   # 验证
```

代码侧仅需一处改动：`SandboxConfig(runtime="runsc")`，`host_config_kwargs()` 会将其透传给
容器创建调用。其代价为冷启动增加约 200–600ms、系统调用变慢，因此务必配合会话级常驻
（必要时叠加预热池）。

---

## 12. 本地验证

```bash
pytest tests/test_sandbox.py -q
```

测试使用 fake Docker client，覆盖：acquire / 复用 / release、容量上限、空闲回收、
workspace 擦除、工具透明性，无需真实容器。

如需端到端验证真实容器，构建镜像后令 `SandboxManager(docker_client=None)` 走默认
`docker.from_env()` 即可。

---

## 附录 A：配置项参考

`SandboxConfig` 全部字段（默认值见 `src/agentengine/sandbox/config.py`）：

| 字段 | 默认值 | 类别 | 说明 |
|---|---|---|---|
| `image` | `agentengine-sandbox:session` | 镜像 | 会话沙盒镜像标签 |
| `runtime` | `None` | 隔离 | 容器 runtime；`None` = runc，`"runsc"` = gVisor |
| `network_disabled` | `True` | 网络 | 默认断网 |
| `user` | `"sandbox"` | 权限 | 非 root 用户（镜像内 uid 1000） |
| `cap_drop` | `("ALL",)` | 权限 | 丢弃全部 Linux capabilities |
| `no_new_privileges` | `True` | 权限 | 禁止提权 |
| `read_only_rootfs` | `False` | 权限 | rootfs 只读；默认关，便于运行期装包 |
| `mem_limit` | `"512m"` | 资源 | 内存上限（cgroup） |
| `nano_cpus` | `2_000_000_000` | 资源 | CPU 上限（纳核，2e9 = 2 核） |
| `pids_limit` | `256` | 资源 | 进程数上限，防 fork 炸弹 |
| `tmpfs` | `{"/tmp": "size=64m"}` | 资源 | 可写临时区 |
| `workspace_mount` | `"/workspace"` | 文件 | 容器内 workspace 挂载点 |
| `default_exec_timeout` | `30.0` | 超时 | 单次执行超时（秒） |
| `max_containers` | `24` | 容量 | 最大并发容器数 |
| `idle_ttl_seconds` | `900.0` | 容量 | 空闲回收阈值（秒），0 表示禁用 |
