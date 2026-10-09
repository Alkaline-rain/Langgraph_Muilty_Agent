"""Coder Agent：负责把用户需求 + LCEL 文档 + 历史反馈转成可运行的代码方案。"""

from langchain_core.prompts import ChatPromptTemplate

from utils.llm_runtime import structured_call
from schemas.models import CodeSolution

# Coder 专属提示词：只负责生成，不负责判断对错
code_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
你是精通 LCEL（LangChain Expression Language）的 Python 开发者。
你的任务：根据用户问题 + 提供的 LCEL 文档，生成完整可运行的代码。

== LCEL 文档 ==
{context}

== 用户问题 ==
{question}

== 历史反馈 ==
{feedback}

输出要求：
1. 先描述解决思路，再列出所有导入语句，最后给出完整可执行的代码块
2. 保证生成的任意代码都可以直接执行（不依赖上下文中未定义的变量）
3. 如果「历史反馈」不为空，请严格对照其中的审查意见和反思建议修改，不要重复犯过的错误
4. 实现同步函数 solve(payload)，返回 JSON 可序列化结果，不要自行调用入口或读取 stdin
5. 仅离线执行；LCEL 使用 langchain_core 的 FakeListLLM 或 RunnableLambda，不调用真实模型服务
6. 不读文件、环境变量或访问网络；不可安装依赖；imports 字段仅放导入语句
""",
        ),
    ]
)


def invoke_coder(
    context: str,
    question: str,
    feedback: str = "",
) -> CodeSolution:
    """调用 Coder Agent 生成代码解决方案。

    Args:
        context: LCEL 文档全文。
        question: 用户原始问题。
        feedback: 已格式化好的历史反馈文本（第一轮为空字符串）。
    """
    return structured_call(code_prompt, CodeSolution, {"context": context, "question": question, "feedback": feedback}, 0.2)
