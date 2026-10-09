执行约定：实现同步函数 solve(payload)，返回 JSON 可序列化的值。
不要主动调用 solve，不要读取 stdin、文件、环境变量或访问网络。
imports 字段只包含导入语句，code 字段包含函数和必要的定义。
镜像预装 langchain-core，支持 ChatPromptTemplate、StrOutputParser、RunnableLambda。
离线模拟模型：from langchain_core.language_models.fake import FakeListLLM
示例链：ChatPromptTemplate.from_template("{text}") | FakeListLLM(responses=["OK"]) | StrOutputParser()
链通过 chain.invoke({"text": payload["text"]}) 调用。
禁止 ChatOpenAI、ChatDeepSeek 等真实模型客户端；生成代码不接触 API Key。
普通 Python 任务可使用白名单中的标准库；不可安装新依赖。
