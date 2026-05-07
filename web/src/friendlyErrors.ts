/**
 * User-facing translations for AgentCoreError codes coming from the SSE error
 * payload's resultMap. Mirror of scripts/renderers/friendly_errors.py — keep
 * the two in sync when adding new codes.
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
    "上游模型限流了，请稍后再试。",
    "如果反复出现，可以降低并发或升级供应商套餐。",
  ],
  llm_timeout: [
    "请求超时",
    "模型在规定时间内没有返回。",
    "可以重试一次，或把问题拆得更短。",
  ],
  llm_connection_error: [
    "无法连接模型服务",
    "网络或上游服务不可用。",
    "检查网络、API 端点 (LLM_BASE_URL) 和供应商状态页。",
  ],
  llm_stream_error: [
    "流式响应中断",
    "模型在生成过程中断开了连接。",
    "重试通常能恢复；持续出现请检查代理或网关。",
  ],
  llm_context_window_exceeded: [
    "对话超出模型上下文",
    "累计输入太长，模型无法继续。",
    "请新开一个会话，或把任务拆小后再试。",
  ],
  llm_http_error: [
    "模型服务返回错误",
    "上游模型返回了非 2xx 响应。",
    "查看 errorMsg / details 里的状态码定位问题。",
  ],
  tool_execution_error: [
    "工具执行失败",
    "工具在执行时抛出了异常。",
    "查看工具结果里的错误细节，或在另一种参数下重试。",
  ],
  agent_cancelled: ["运行已取消", "用户中止了任务。", ""],
  runtime_execution_error: [
    "运行时错误",
    "Agent 运行过程中遇到了未预期的状态。",
    "如果反复出现，请抓 logs/runs/<日期>/<run_id>.jsonl 排查。",
  ],
  unexpected_error: [
    "未预期错误",
    "发生了一个未分类的异常。",
    "请把错误信息和重现步骤反馈给开发者。",
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
