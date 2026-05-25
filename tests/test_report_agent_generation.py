from __future__ import annotations

import pytest

from agentengine.llm.interfaces import LLMResponse
from examples.services.reporting import jobs
from examples.services.reporting.jobs import ReportJobStore, parse_model_report_blocks, stream_report_artifact


def test_parse_model_report_blocks_accepts_json_object_and_chart() -> None:
    content = """
    ```json
    {
      "blocks": [
        {"type": "heading", "level": 1, "text": "自定义财务报告"},
        {"type": "paragraph", "text": "收入增长来自上传文件中的 revenue 字段。"},
        {
          "type": "chart",
          "text": "收入趋势",
          "chart": {
            "kind": "line",
            "title": "收入趋势",
            "x": ["2023Q1", "2024Q1"],
            "series": [{"name": "revenue", "data": [698, 805]}],
            "y_format": "currency"
          }
        }
      ]
    }
    ```
    """

    blocks = parse_model_report_blocks(content)

    assert [block.type for block in blocks] == ["heading", "paragraph", "chart"]
    assert blocks[0].text == "自定义财务报告"
    assert blocks[2].chart_id
    assert blocks[2].chart is not None
    assert blocks[2].chart.series[0].data == [698, 805]


def test_parse_model_report_blocks_rejects_non_blocks_payload() -> None:
    with pytest.raises(ValueError, match="blocks array"):
        parse_model_report_blocks('{"message":"not a report"}')


def test_report_job_store_preserves_selected_skill() -> None:
    store = ReportJobStore()

    job = store.create(
        conversation_id="conv",
        title="Report",
        intent="根据文件生成收入质量分析",
        skill="data_analysis",
    )

    assert job.skill == "data_analysis"
    assert job.snapshot()["skill"] == "data_analysis"


@pytest.mark.asyncio
async def test_report_stream_uses_llm_blocks_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLLM:
        async def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            assert "Uploaded file context" in messages[1].content
            return LLMResponse(
                content='{"blocks":[{"type":"heading","level":1,"text":"模型定制报告"},{"type":"paragraph","text":"按用户提示生成。"}]}'
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(jobs, "create_llm_from_env", lambda required=False: FakeLLM())
    store = ReportJobStore()
    job = store.create(
        conversation_id="conv",
        title="Custom",
        intent="只输出经营现金流质量分析",
        skill="data_analysis",
    )

    events = [event async for event in stream_report_artifact(job=job, request_id="report-test")]

    block_events = [event for event in events if event["event"] == "artifact_block_added"]
    assert block_events[0]["data"]["text"] == "模型定制报告"
    assert block_events[1]["data"]["text"] == "按用户提示生成。"
