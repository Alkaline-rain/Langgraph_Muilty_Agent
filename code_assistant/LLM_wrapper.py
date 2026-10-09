from langchain_community.document_loaders.recursive_url_loader import RecursiveUrlLoader
from bs4 import BeautifulSoup as bs
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek
import os
from dotenv import load_dotenv

load_dotenv()

# RAG
url = "https://python.langchain.ac.cn/docs/concepts/lcel/"
loader = RecursiveUrlLoader(
    url=url, max_depth=1, extractor=lambda x:bs(x, features="html.parser").text
)
docs = loader.load()
d_sort = sorted(docs, key=lambda x:x.metadata["source"])
d_reverse = list(reversed(d_sort))
content = "\n\n\n --- \n\n\n".join([x.page_content for x in d_reverse])

# 评分提示词
code_gen_prompt = ChatPromptTemplate.from_messages([
    ("system", """你是一个精通LECL的编程助手, 
    你的任务是根据LCEL文档 \n --- \n {context} \n --- \n 回答用户的问题，确保你提供的任何代码都能够执行,\n
    并且包含必要的导入包和已定义的变量。请按照以下组织结构回答问题：首先描述代码的解决方案，\n
    然后列出导入的语句，最后给出功能完整的代码块，以下是用户的问题："""),
    ("placeholder", "{messages}"),
])

# LLM
class code(BaseModel):
    prefix: str = Field(default="对用户问题的解决方案和描述")
    imports: str = Field(default="导入的语句")
    code: str = Field(default="除导入语句外功能完整的代码块")

    llm = ChatDeepSeek(
        model="deepseek-v4-pro",
        temperature=0,
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.cn/v1",
    )
    code_gen_chain = code_gen_prompt | llm
    
