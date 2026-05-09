# 指南：创建新 Tool

> 从继承 `Tool` 到注册、接入 Agent，完整演示如何开发一个自定义工具。

---

## 步骤 1：实现 Tool 类

创建 `src/agentengine/tools/builtin/my_tool.py`：

```python
from agentengine.tools.base import Tool

class WeatherTool(Tool):
    name = "get_weather"
    description = "Get current weather for a given city."
    schema = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name, e.g. 'Beijing'"
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "Temperature unit"
            }
        },
        "required": ["city"]
    }
    timeout_seconds = 10.0
    is_destructive = False
    max_result_chars = 2000

    async def run(self, **kwargs) -> str:
        city = kwargs.get("city", "")
        unit = kwargs.get("unit", "celsius")

        # 这里调用真实天气 API
        # 示例返回模拟数据
        return f"Weather in {city}: 22°{unit[0].upper()}, sunny."
```

---

## 步骤 2：注册工具

在 `src/agentengine/tools/builtin/__init__.py` 或其他合适位置：

```python
from agentengine.tools.registry import register_tool
from agentengine.tools.builtin.my_tool import WeatherTool

register_tool("get_weather")(WeatherTool)
```

---

## 步骤 3：接入 Agent

在 Agent 的 `setup` 中：

```python
from agentengine.base.context import AgentContext
from agentengine.tools.builtin.my_tool import WeatherTool

async def _setup(context: AgentContext) -> None:
    context.tool_collection.add(WeatherTool())
```

---

## 步骤 4：测试

```bash
PYTHONIOENCODING=utf-8 uv run python scripts/chat_pretty.py my_agent "北京今天天气怎么样？"
```

---

## 开发规范

### 1. 参数校验

```python
async def run(self, **kwargs) -> str:
    city = str(kwargs.get("city", "")).strip()
    if not city:
        return "Error: 'city' is required."
    ...
```

### 2. 异常处理

工具内部捕获异常，返回错误字符串，**不要抛异常**：

```python
async def run(self, **kwargs) -> str:
    try:
        result = await call_api(...)
        return result
    except Exception as e:
        return f"Error: {e}"
```

### 3. 结果大小控制

设置合理的 `max_result_chars`，防止撑爆 Memory：

```python
max_result_chars = 4000   # 默认 8000，大数据量工具可减小
result_summary_strategy = "head_tail"  # 大结果保留头尾
```

### 4. 超时设置

```python
timeout_seconds = 10.0   # 网络工具建议 10-30s
timeout_seconds = 5.0    # 本地文件操作建议 5s
```

### 5. 破坏性标记

会修改数据或状态的工具设为 `is_destructive = True`：

```python
class DeleteFileTool(Tool):
    name = "delete_file"
    is_destructive = True   # 触发审批门
```

---

## 高级：StreamingTool

需要边执行边输出中间结果的工具：

```python
from agentengine.tools.base import StreamingTool, ToolStreamEvent

class SearchTool(StreamingTool):
    name = "web_search"

    async def run_stream(self, **kwargs):
        query = kwargs.get("query", "")

        yield ToolStreamEvent(event_type="tool_thought", data=f"Searching for: {query}")

        for i, result in enumerate(await self._do_search(query)):
            yield ToolStreamEvent(
                event_type="search_result",
                data={"title": result.title, "url": result.url}
            )

        yield ToolStreamEvent(
            event_type="final_result",
            data="Search completed.",
            is_final=True
        )
```

---

## 检查清单

- [ ] `name` 全局唯一
- [ ] `description` 清楚说明什么时候该用
- [ ] `schema` 正确定义参数
- [ ] `timeout_seconds` 设置合理
- [ ] `is_destructive` 正确标记
- [ ] `run()` 内部捕获异常
- [ ] 已注册到 Registry
- [ ] Agent setup 中已 add
