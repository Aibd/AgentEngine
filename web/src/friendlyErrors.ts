/**
 * User-facing translations for AgentEngineError codes coming from SSE v2 error
 * data. Mirror of scripts/renderers/friendly_errors.py - keep the two in sync
 * when adding new codes.
 */

export type FriendlyError = {
  headline: string;
  detail: string;
  hint: string;
  retryable: boolean;
};

const TEMPLATES: Record<string, [string, string, string]> = {
  llm_rate_limited: [
    "服务繁忙",
    "模型服务正在限流，请稍后再试。",
    "稍等片刻后重试，或降低请求频率。",
  ],
  llm_timeout: [
    "请求超时",
    "模型响应时间过长。",
    "可以重试，或缩短提示词和工具步骤。",
  ],
  llm_connection_error: [
    "连接失败",
    "无法连接到配置的模型服务。",
    "检查 LLM_BASE_URL 和网络连接。",
  ],
  llm_stream_error: [
    "流式响应中断",
    "模型流式响应意外结束。",
    "请重试；如果持续出现，检查模型服务日志。",
  ],
  llm_context_window_exceeded: [
    "上下文过长",
    "提示词超过了模型上下文窗口。",
    "减少输入内容，或新开会话后再试。",
  ],
  llm_usage_limit_reached: [
    "Usage limit reached",
    "The LLM provider reported that the account or project usage limit is exhausted.",
    "Check billing, quotas, or switch to a different configured provider.",
  ],
  llm_http_error: [
    "模型接口错误",
    "模型 API 返回了非成功响应。",
    "查看 SSE error data 和 details 字段。",
  ],
  tool_execution_error: [
    "工具执行失败",
    "工具运行时抛出了错误。",
    "检查工具结果和 runtime JSONL 日志。",
  ],
  agent_cancelled: ["运行已取消", "本次运行已取消。", ""],
  runtime_execution_error: [
    "运行失败",
    "Agent 运行时发生错误。",
    "查看 logs/runs/<date>/<run_id>.jsonl 获取详情。",
  ],
  unexpected_error: [
    "未知错误",
    "发生了未预期的错误。",
    "检查运行日志和堆栈信息。",
  ],
};

export function translateError(payload: unknown): FriendlyError {
  const obj = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
  const code = String(obj.code ?? "");
  const message = String(obj.message ?? "");
  const retryable = Boolean(obj.retryable);

  const template = TEMPLATES[code];
  if (!template) {
    return {
      headline: "出错了",
      detail: message || "未知错误",
      hint: "",
      retryable,
    };
  }
  const [headline, baseDetail, hint] = template;
  const detail = message && !baseDetail.includes(message) ? `${baseDetail}\n${message}` : baseDetail;
  return { headline, detail, hint, retryable };
}
