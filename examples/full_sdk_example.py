from __future__ import annotations

"""
完整 AgentEngine 使用示例

这个脚本演示一条接近真实业务接入的完整链路：
1. 从 .env 或命令行参数读取 LLM 配置
2. 创建 OpenAI 兼容的 LLM 客户端
3. 声明一个自定义工具 OrderStatusTool
4. 通过 AgentPreset 注册 Agent 的系统指令和启动钩子
5. 使用 AgentEngine 创建流式上下文并运行 Agent
6. 将会话消息和运行记录保存到 SQLite

运行示例：
    uv run --env-file .env python examples/full_sdk_example.py

如果不使用 .env，也可以通过 --base-url、--model、--api-key 等参数显式传入配置。
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# examples 目录通常不是安装后的包路径。这里把项目根目录和 src 加入 sys.path，
# 方便直接从源码仓库运行示例，而不需要先 pip install 当前项目。
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentengine import AgentContext, AgentEngine, AgentPreset, Tool  # noqa: E402
from agentengine.llm.openai_compat import OpenAICompatibleClient  # noqa: E402
from agentengine.persistence import SqlitePersistence  # noqa: E402


class OrderStatusTool(Tool):
    """示例工具：根据订单号查询一个写死的演示订单状态"""

    # 工具名称会暴露给 LLM。模型需要通过这个名字发起 tool call。
    name = "lookup_order_status"

    # 工具描述会进入 OpenAI tools schema，直接影响模型什么时候选择调用它。
    description = "Look up a demo order by order_id."

    # JSON Schema 定义工具参数。这里要求模型传入一个 order_id 字符串。
    schema = {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "Order id such as A-100 or B-200.",
            }
        },
        "required": ["order_id"],
    }

    async def run(self, **kwargs: Any) -> str:
        """执行工具逻辑，返回值会作为 tool_result 回传给 LLM"""

        # LLM 传入的参数来自 schema，但真实业务里仍建议做类型转换和清洗。
        order_id = str(kwargs.get("order_id", "")).strip().upper()

        # 演示用的内存数据。真实项目里通常会在这里查询数据库、HTTP API 或内部 RPC。
        orders = {
            "A-100": "packed and waiting for carrier pickup",
            "B-200": "out for delivery",
            "C-300": "delivered yesterday",
        }
        return orders.get(order_id, f"no demo order found for {order_id or '<empty>'}")


async def setup_tools(context: AgentContext) -> None:
    """AgentPreset.setup 钩子：每次 run 开始前，把本次运行可用的工具注册进去"""

    context.tool_collection.add(OrderStatusTool())


def load_dotenv(path: Path) -> None:
    """极简 .env 加载器，只处理 KEY=VALUE，不覆盖已经存在的环境变量"""

    if not path.exists():
        return
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_args() -> argparse.Namespace:
    """读取命令行参数，并先加载项目根目录下的 .env"""

    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Run a complete AgentEngine example with streaming and persistence."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        help="OpenAI-compatible API host, for example https://api.deepseek.com",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL", "deepseek-v4-pro"),
        help="Model name sent in the chat completions payload.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("LLM_API_KEY", ""),
        help="Provider API key. Prefer LLM_API_KEY in .env for local runs.",
    )

    # timeout 和 max_retries 直接传给 OpenAICompatibleClient，
    # 用于控制 HTTP 请求超时和失败重试次数。
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=2)

    # query 是本次用户输入。默认问题会诱导模型调用 lookup_order_status 工具。
    parser.add_argument(
        "--query",
        default="Where is order A-100? Use the order lookup tool if helpful.",
    )

    # agent-name 必须和 build_engine() 中注册到 presets 的 key 一致。
    parser.add_argument("--agent-name", default="support_agent")

    # conversation-id 非空时会启用会话历史加载、保存和会话锁。
    # 多次用同一个 conversation-id 运行，可以看到历史消息累积到 SQLite 中。
    parser.add_argument("--conversation-id", default="example-conversation")

    # request-id 用于追踪一次运行，也用于中断控制。为空时下面会自动生成。
    parser.add_argument("--request-id", default="")

    # SQLite 文件路径。示例会把消息历史和 run 记录保存到这个文件。
    parser.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "agentengine_example.db",
    )
    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Print reasoning_content chunks when the provider emits them.",
    )
    return parser.parse_args()


def build_llm_client(args: argparse.Namespace) -> OpenAICompatibleClient:
    """根据命令行参数创建 OpenAI 兼容客户端"""

    if not args.api_key:
        raise SystemExit("Missing API key. Set LLM_API_KEY or pass --api-key.")

    # OpenAICompatibleClient 只要求服务端兼容 OpenAI Chat Completions 风格接口。
    # base_url 可以指向 OpenAI、DeepSeek、企业代理网关或其他兼容服务。
    return OpenAICompatibleClient(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )


def build_engine(persistence: SqlitePersistence, agent_name: str) -> AgentEngine:
    """创建 AgentPreset，并把它注册到 AgentEngine"""

    # AgentPreset 是业务侧声明 Agent 的主要入口：
    # - name：Agent 名称
    # - instructions：业务系统提示词
    # - setup：运行前钩子，用于注册工具或初始化上下文
    # - max_messages / auto_compact_tokens：控制历史消息管理
    preset = AgentPreset(
        name=agent_name,
        description="Demo support agent with one custom tool.",
        instructions=(
            "You are a concise support assistant. When the user asks about an "
            "order, call lookup_order_status with the order id, then explain "
            "the result in one or two sentences."
        ),
        max_messages=40,
        auto_compact_tokens=120_000,
        setup=setup_tools,
    )

    # presets 是一个 name -> AgentPreset 的映射。engine.run(agent_name=...) 会按名称取配置。
    # persistence 注入后，conversation_id 非空时引擎会自动加载/保存会话消息。
    return AgentEngine(presets={agent_name: preset}, persistence=persistence)


def print_stream_frame(frame: dict[str, Any], *, show_thinking: bool) -> None:
    """把 SseEventQueue 里的事件帧打印成人类可读的命令行输出"""

    # SSE comment 帧通常用于心跳或辅助信息，这个命令行示例直接忽略。
    if "comment" in frame:
        return

    event = str(frame.get("event", ""))
    data = frame.get("data", {})
    payload = data if isinstance(data, dict) else {}

    # 以下事件名来自 agentengine 的流式事件协议：
    # start：运行开始
    # step：进入新的 ReAct 轮次
    # thinking：模型推理增量，只有部分模型/服务商会返回
    # text：最终回复文本增量
    # tool_call_start / tool_result：工具调用开始与结果
    # usage：Token 用量
    # done / error：运行结束或失败
    if event == "start":
        print(f"[start] agent={payload.get('agent')} request_id={payload.get('request_id')}")
    elif event == "step":
        print(f"\n[step {payload.get('turn')}]")
    elif event == "thinking" and show_thinking:
        print(str(payload.get("delta", "")), end="", flush=True)
    elif event == "text":
        print(str(payload.get("delta", "")), end="", flush=True)
    elif event == "tool_call_start":
        print(f"\n[tool] {payload.get('tool')}({payload.get('arguments')})")
    elif event == "tool_result":
        print(f"[tool result] {payload.get('result')}")
    elif event == "usage":
        print(
            "\n[usage] "
            f"prompt={payload.get('prompt_tokens')} "
            f"completion={payload.get('completion_tokens')} "
            f"total={payload.get('total_tokens')}"
        )
    elif event == "done":
        print(f"\n[done] reason={payload.get('reason')}")
    elif event == "error":
        print(f"\n[error] {payload.get('code')}: {payload.get('message')}")


async def run_streaming_example(args: argparse.Namespace) -> None:
    """运行一次完整的流式 Agent 示例"""

    # 确保 SQLite 数据库目录存在。
    args.db_path.parent.mkdir(parents=True, exist_ok=True)

    # 持久化层：负责保存会话消息、运行记录和工具制品。
    persistence = SqlitePersistence(args.db_path)

    # LLM 客户端和引擎是分开的。这样同一个引擎可以按请求注入不同 LLM。
    llm = build_llm_client(args)
    engine = build_engine(persistence, args.agent_name)

    # request_id 标识本次运行。实际 Web 服务中一般来自请求 ID 或 trace ID。
    request_id = args.request_id or f"example-{uuid.uuid4().hex[:12]}"

    # create_streaming_context 会创建：
    # - context：传给 engine.run() 的运行上下文
    # - event_stream：异步事件队列，engine.run() 运行过程中会不断往里面写事件
    context, event_stream = engine.create_streaming_context(
        request_id=request_id,
        query=args.query,
        conversation_id=args.conversation_id,
    )

    # 这里演示“按请求注入 LLM”。如果不设置 context.llm，也可以在 AgentEngine 上配置 llm_factory。
    context.llm = llm

    async def run_and_close_stream() -> str:
        """后台运行 Agent，并确保运行结束后关闭事件流"""

        try:
            return await engine.run(
                agent_name=args.agent_name,
                query=args.query,
                context=context,
            )
        finally:
            await event_stream.close()

    # engine.run() 放到后台任务中执行；前台同时消费 event_stream，实现流式输出。
    task = asyncio.create_task(run_and_close_stream())

    try:
        # 实时读取并打印流式事件。Web 服务中通常会把这些 frame 转成 SSE/WebSocket 消息。
        async for frame in event_stream:
            print_stream_frame(frame, show_thinking=args.show_thinking)

        # 流结束后等待后台任务拿到最终回答字符串。
        final_answer = await task

        # 验证持久化效果：读取当前 conversation_id 下的消息和最近一次运行记录。
        messages = await persistence.load_messages(args.conversation_id)
        runs = await persistence.list_runs(args.conversation_id, limit=1)

        print("\n\nFinal answer:")
        print(final_answer)
        print(f"\nPersisted SQLite DB: {args.db_path}")
        print(f"Persisted messages: {len(messages)}")
        if runs:
            print(f"Latest run id: {runs[0]['run_id']}")
    finally:
        # 释放底层资源。真实服务通常在应用 shutdown 钩子里统一关闭。
        await engine.close()
        await llm.close()


def main() -> None:
    """同步入口：解析参数，然后启动 asyncio 事件循环"""

    asyncio.run(run_streaming_example(parse_args()))


if __name__ == "__main__":
    main()
