"""Reflector Agent：负责从更高维度分析失败根本原因，给出新的解决策略。"""

from langchain_core.prompts import ChatPromptTemplate

from utils.llm_runtime import structured_call
from schemas.models import ReflectionResult

# Reflector 专属提示词：做「元思考」，分析思路层面的偏差
reflect_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
你是一位资深软件架构师，专门分析代码生成失败的根本原因。
你的任务：当 Coder 的方案被 Reviewer 驳回后，从更高维度分析问题。

== 用户问题 ==
{question}

== 本轮尝试次数 ==
第 {iteration} 次

== 审查结果 ==
问题列表：
{issues}
修改建议：
{suggestions}

== 历史失败记录 ==
{attempt_summary}

请回答：
1. 根本原因是什么？（不只是语法错误，而是思路偏差）
2. 需要换一种什么新思路来解决问题？
3. 下一次生成时，具体要在哪些地方做出改变？

你的输出将直接指导 Coder 重新生成代码，请务必具体、可执行。
""",
        ),
    ]
)


def invoke_reflector(
    question: str,
    iteration: int,
    issues: list[str],
    suggestions: list[str],
    attempt_summary: str,
) -> ReflectionResult:
    """调用 Reflector Agent 反思失败原因并给出新策略。

    Args:
        question: 用户原始问题。
        iteration: 当前迭代次数。
        issues: 当前轮审查发现的问题列表。
        suggestions: 当前轮审查给出的修改建议列表。
        attempt_summary: 历史失败记录的简要摘要。
    """
    return structured_call(reflect_prompt, ReflectionResult, {"question": question, "iteration": iteration, "issues": "\n".join(issues), "suggestions": "\n".join(suggestions), "attempt_summary": attempt_summary}, 0.4)
