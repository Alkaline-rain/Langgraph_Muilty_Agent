"""全局配置：加载环境变量，提供 LLM 工厂函数。

所有 Agent 通过 get_llm() 创建自己的 LLM 实例，
用不同的 temperature 体现不同的角色特性：
- Coder     0.2  需要一定创造力
- Reviewer  0.0  审查要确定、稳定
- Reflector 0.4  需要洞察力但不能太发散
"""

import os

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

load_dotenv()

MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")


def get_llm(temperature: float = 0.0, max_tokens: int = 2048) -> ChatDeepSeek:
    """创建 DeepSeek LLM 实例。

    Args:
        temperature: 采样温度。不同 Agent 传入不同值。
    """
    return ChatDeepSeek(
        model=MODEL_NAME,
        temperature=temperature,
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_API_URL"),
        max_tokens=max_tokens,
        max_retries=0,
        timeout=60,
    )
