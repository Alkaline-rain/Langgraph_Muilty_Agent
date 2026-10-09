from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langchain_community.chat_models import ChatDeepseek
import os
from dotenv import load_dotenv
from langchain_community.document_loaders.recursive_url_loader import RecursiveUrlLoader
from bs4 import BeautifulSoup as bs

load_dotenv()

# LCEL 文档
url = "https://python.langchain.ac.cn/docs/concepts/lcel/"
loader = RecursiveUrlLoader(
    url=url, max_depth=1, extractor=lambda x: bs(x, "html.parser").text
    
)
docs = loader.load()
d_sort = sorted(docs, key=lambda x: x.metadata["source"])
d_reverse = list(reversed(d_sort))

content = "\n\n\n --- \n\n\n".join(
    d.page_content for d in d_reverse
)

# 评价助手提示
code_gen_prompt = ChatPromptTemplate.from_messages(
    ("system", "你是一个精通LCEL（Language Chain Extension Language）的编程助手。\n"
    "这是LECL文档：\n --- \n {content} \n --- \n "
    "请根据上述文档回答用户的问题，保证你生成的任意代码都可以执行，\n"
    "包含所有导入和已定义的变量。请按照以下组织结构回答问题：\n"
    "首先描述代码解决方案，然后列出导入语句，最后给出功能完整的代码块\n"
    "以下是用户的问题："),
    ("placeholder", "{messages}"),
)

# 代码模型
class code(BaseModel):
    """LECL 问题代码的解决方案和描述"""
    prefix: str = Field(description="问题的解决方案和描述")
    imports: str = Field(description="导入语句")
    code: str = Field(description="不包含导入语句的功能完整的代码块")

    llm = ChatDeepseek(
        model="deepseek-v4-pro",
        temperature=0,
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_API_URL"),
    )
    # 用chain链接prompt和llm
    code_gen_chain = code_gen_prompt | llm.with_structured_output(code)
    
