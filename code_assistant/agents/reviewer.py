"""Reviewer Agent：负责审查代码，找出问题并给出修改建议。只审查，不修改。"""

from langchain_core.prompts import ChatPromptTemplate

from utils.llm_runtime import structured_call
from schemas.models import ReviewResult

# Reviewer 专属提示词：只挑毛病、给建议，不负责改代码
review_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
你是一位细致的代码审查员。
你的任务：审查生成的代码，找出所有问题。

== 用户问题 ==
{question}

== 待审查的代码 ==
{code_to_review}

== 执行结果 ==
{exec_result}

审查维度：
1. 语法正确性
2. 导入语句完整性
3. LCEL 用法是否符合文档规范
4. 逻辑是否正确

输出要求：
- passes: 代码是否通过审查
- issues: 发现的问题列表（若执行失败，结合报错信息定位具体原因）
- suggestions: 具体的修改建议（精确到变量名 / 函数调用）

注意：你只负责审查和给出建议，不负责修改代码。
""",
        ),
    ]
)


def invoke_reviewer(
    question: str,
    code_to_review: str,
    exec_result: str,
) -> ReviewResult:
    """调用 Reviewer Agent 审查代码。

    Args:
        question: 用户原始问题。
        code_to_review: 已格式化的代码文本（描述 + 导入 + 代码块）。
        exec_result: 执行结果的描述（成功或错误类型 + 错误信息）。
    """
    return structured_call(review_prompt, ReviewResult, {"question": question, "code_to_review": code_to_review, "exec_result": exec_result}, 0.0)
