---
name: web-search
description: 搜索互联网，返回相关网页的标题、链接和摘要。适合查找最新信息、事实核查、研究调查。
host_exec: true
---

# Web Search

使用 Tavily 搜索引擎检索互联网内容，返回标题、URL 和内容摘要。

## 使用场景
- 查找最新新闻、行情、事件
- 事实核查
- 研究特定主题背景

## 执行方式

调用 `RunSkillScript` 执行：

```json
{
  "skill": "web-search",
  "script": "scripts/search.py",
  "args": ["搜索词", "5"]
}
```

**参数：**
1. `query`（必填）：搜索词
2. `max_results`（可选，默认 5，范围 1-10）：返回结果数量

**输出格式：** 每条结果包含编号、标题、URL 和内容摘要。

Arguments: ${ARGUMENTS}
