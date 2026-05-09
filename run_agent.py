#!/usr/bin/env python3
"""
快速运行Agent的脚本，直接使用真实LLM
"""

import asyncio
import os
import sys
from pathlib import Path

# 确保项目路径在Python路径中
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


async def run_quick_test():
    """快速测试真实LLM调用"""
    print("🚀 Agent Core Refactor - 真实LLM测试")
    print("=" * 60)
    
    # 检查环境变量
    required_vars = ["LLM_API_KEY", "LLM_MODEL"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print(f"❌ 缺少必要的环境变量: {', '.join(missing)}")
        print("请确保在.env文件或环境变量中设置了:")
        for var in missing:
            print(f"  - {var}")
        return
    
    print("✅ 环境变量检查通过")
    print(f"   LLM_MODEL: {os.getenv('LLM_MODEL', '未设置')}")
    print(f"   LLM_BASE_URL: {os.getenv('LLM_BASE_URL', '默认(DeepSeek)')}")
    
    # 导入项目模块
    try:
        from agent_core.base.context import AgentContext
        from agent_core.llm.factory import create_llm_from_env
        from services.agent_orchestration_service import AgentOrchestrationService
    except ImportError as e:
        print(f"❌ 导入错误: {e}")
        print("请确保已安装依赖: uv sync --extra dev")
        return
    
    # 创建LLM客户端
    print("\n⏳ 1. 创建LLM客户端...")
    try:
        llm = create_llm_from_env(required=True)
        print(f"✅ LLM客户端创建成功")
        print(f"   模型: {llm.model}")
        print(f"   端点: {llm.base_url}")
    except Exception as e:
        print(f"❌ 创建LLM客户端失败: {e}")
        return
    
    # 创建服务和上下文
    print("\n⏳ 2. 初始化服务...")
    service = AgentOrchestrationService()
    
    context, event_stream = service.create_streaming_context(
        request_id="quick-test-001",
        query="请用中文简要介绍一下这个项目的核心功能",
        conversation_id="test-conv-001"
    )
    context.llm = llm
    
    print("✅ 服务初始化完成")
    
    # 运行代理
    print("\n⏳ 3. 运行通用聊天代理...")
    print("   查询: '请用中文简要介绍一下这个项目的核心功能'")
    print("   (这可能需要一些时间，取决于LLM响应速度)")
    
    try:
        result = await service.run(
            agent_name="general_chat",
            query="请用中文简要介绍一下这个项目的核心功能",
            context=context
        )
        
        print("\n✅ 4. 代理运行成功!")
        print("=" * 60)
        print("📋 回复结果:")
        print(result)
        print("=" * 60)
        
        # 显示运行信息
        print("\n📊 运行统计:")
        print(f"   状态: {context.extras.get('agent_state', '未知')}")
        print(f"   步骤数: {context.extras.get('agent_current_step', 0)}")
        print(f"   会话ID: {context.conversation_id}")
        
    except Exception as e:
        print(f"\n❌ 代理运行失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 关闭资源
    print("\n⏳ 5. 清理资源...")
    try:
        await llm.close()
        print("✅ 资源清理完成")
    except Exception as e:
        print(f"⚠️  资源清理警告: {e}")
    
    print("\n🎉 测试完成!")
    print("=" * 60)
    print("💡 下一步:")
    print("   1. 尝试运行深度研究代理: python scripts/chat.py deep_research \"你的查询\"")
    print("   2. 启用流式输出: python scripts/chat.py general_chat \"你的查询\" --trace")
    print("   3. 集成到你的应用中: from services import AgentOrchestrationService")

if __name__ == "__main__":
    asyncio.run(run_quick_test())
