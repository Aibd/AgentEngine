---
name: web-fetch
description: 抓取指定 URL 的网页内容，自动转换为纯文本返回。适合阅读文档、新闻、产品页面等。
host_exec: true
---

# Web Fetch

抓取指定 URL 的内容并以纯文本形式返回，HTML 标签自动剥离。

## 使用场景
- 阅读某篇具体的文章、新闻或文档
- 获取某个页面的详细内容（搭配 web-search 使用：先搜再读）
- 抓取 API 返回的 JSON 数据

## 执行方式

调用 `RunSkillScript` 执行：

```json
{
  "skill": "web-fetch",
  "script": "scripts/fetch.py",
  "args": ["https://example.com", "8000"]
}
```

**参数：**
1. `url`（必填）：要抓取的完整 URL（须以 http:// 或 https:// 开头）
2. `max_chars`（可选，默认 8000，最大 32000）：返回内容的字符数上限

Arguments: ${ARGUMENTS}
