#!/usr/bin/env python3
"""
示例：在代码中直接调用Agent Core
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / "src"))


def _load_dotenv(path: Path) -> None:
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


_load_dotenv(Path(__file__).parent / ".env")


async def run_example():
    """运行示例"""
    print("🚀 Agent Core 示例运行")
    print("=" * 50)
    
    # 检查环境变量
    print("1. 检查环境变量...")
    required = ["LLM_API_KEY", "LLM_MODEL"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        print(f"❌ 缺少环境变量: {', '.join(missing)}")
        print("请设置后重试")
        return
    print("✅ 环境变量检查通过")
    
    # 导入模块
    print("\n2. 导入模块...")
    try:
        from agent_core.base.context import AgentContext
        from agent_core.llm.factory import create_llm_from_env
        from services.agent_orchestration_service import AgentOrchestrationService
        print("✅ 模块导入成功")
    except Exception as e:
        print(f"❌ 模块导入失败: {e}")
        return
    
    # 创建服务
    print("\n3. 创建服务...")
    try:
        service = AgentOrchestrationService()
        print("✅ 服务创建成功")
    except Exception as e:
        print(f"❌ 服务创建失败: {e}")
        return
    
    # 创建LLM客户端
    print("\n4. 创建LLM客户端...")
    try:
        llm = create_llm_from_env(required=True)
        print(f"✅ LLM客户端创建成功")
        print(f"   模型: {llm.model}")
        print(f"   端点: {llm.base_url}")
    except Exception as e:
        print(f"❌ LLM客户端创建失败: {e}")
        return
    
    # 运行查询
    print("\n5. 运行查询...")
    query = "用一句话介绍这个项目"
    print(f"   查询: '{query}'")
    print("   (请等待LLM响应...)")
    
    try:
        context, event_stream = service.create_streaming_context(
            request_id="example-run",
            query=query,
            conversation_id="example-conv"
        )
        context.llm = llm
        
        result = await service.run(
            agent_name="general_chat",
            query=query,
            context=context
        )
        
        print("\n6. 结果:")
        print("=" * 50)
        print(result)
        print("=" * 50)
        
        print(f"\n📊 运行信息:")
        print(f"   状态: {context.extras.get('agent_state', '未知')}")
        print(f"   步骤: {context.extras.get('agent_current_step', 0)}")
        
    except Exception as e:
        print(f"\n❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 清理资源
    print("\n7. 清理资源...")
    try:
        await llm.close()
        print("✅ 资源清理完成")
    except Exception as e:
        print(f"⚠️  资源清理警告: {e}")
    
    print("\n🎉 示例运行完成！")
    print("\n💡 下一步:")
    print("   - 查看 RUNNING_GUIDE.md 了解更多用法")
    print("   - 尝试其他代理: deep_research")
    print("   - 启用流式输出: --trace 参数")

if __name__ == "__main__":
    asyncio.run(run_example())
