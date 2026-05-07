# Trace UI 策略

目标：将一次用户请求渲染为可读的 Agent 追踪记录，类似于 Claude Code / Codex 静态卡片，并带有 Kimi 风格的思考通道。追踪不是原始日志。它是一个执行故事，开始时是实时的，完成后成为稳定的记录。

## 产品形态

- 首屏是活跃对话和追踪，而不是落地页。
- 每次运行是一叠垂直的静态卡片。
- 卡片在运行期间可以是实时的，完成后冻结。
- 最终答案在视觉上与中间工作区分开。
- 工具卡片展示调用了什么、参数是什么、耗时、成功或失败，以及紧凑的结果预览。

## 事件到 UI 的映射

| 流式事件 | UI 处理方式 |
| --- | --- |
| `start` | 用用户请求创建一个运行容器。 |
| `step` | 打开一个新的步骤组。 |
| `thinking` | 追加到当前步骤内可折叠的思考块中。 |
| `text` | 追加助手回答文本。在中间轮次中，保持在当前步骤内。 |
| `tool_call_start` | 用 `tool_call_id` 创建一个待处理的工具卡片。 |
| `tool_result` | 完成匹配的工具卡片并冻结其内容。 |
| `step_end` | 标记步骤完成；保留 `has_tool_calls` 和耗时。 |
| `usage` | 将 Token 和耗时摘要附加到运行页脚。 |
| `result` | 标记运行完成并显示最终答案卡片。 |
| `error` | 标记运行失败并用结构化错误冻结追踪。 |

## 思考显示策略

后端将 `reasoning_content` 暴露为 `thinking` 事件。Web UI 应将其视为面向用户的进度通道，而不是第二个最终答案。

- 步骤完成后默认折叠。
- 当前步骤正在活跃运行时展开。
- 按顺序保留短文本块。
- 允许以后用总结性推理替换，而不改变事件协议。

## React 数据模型

使用基于 SSE 事件的 reducer：

```ts
type RunTrace = {
  requestId: string;
  conversationId: string;
  status: "running" | "completed" | "failed";
  steps: StepTrace[];
  usage?: UsageSummary;
  finalText?: string;
};

type StepTrace = {
  turn: number;
  status: "running" | "completed";
  thinking: string[];
  text: string[];
  tools: ToolTrace[];
  hasToolCalls?: boolean;
  elapsedSeconds?: number;
};

type ToolTrace = {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: "running" | "completed" | "failed";
  result?: unknown;
  elapsedSeconds?: number;
  errorType?: string;
};
```

## 阶段说明

- 阶段 1 锁定协议顺序和黄金 SSE 信封。
- 阶段 2 应展示带 `ReadFileTool` 的终端渲染器。
- 阶段 3 构建 React 追踪 reducer 和卡片渲染器，先针对录制的 fixture，然后是实时 SSE。
- 阶段 4 增加生产级打磨：取消、重试、截断 UX、空状态和可访问性。
- 阶段 5 更新文档并将协议 fixture 保留为兼容性测试。
