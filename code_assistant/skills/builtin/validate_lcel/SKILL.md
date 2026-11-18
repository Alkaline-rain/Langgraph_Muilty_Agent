# 离线 LCEL 验证
版本：1.0.0。静态检查代码，只使用镜像中已有的 langchain_core。
需要接口知识时由规划器选择 retrieve_domain_docs；此能力不再注入固定文档。
模型节点使用 FakeListLLM / RunnableLambda。先静态检查，再通过 verify_cases 执行。
不允许候选代码调用在线模型；行为正确不证明内部一定用了 LCEL。
