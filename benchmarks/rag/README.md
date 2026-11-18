# RAG 种子评测集

- `queries.json`：30 条查询，按主题族拆分 dev/test，relevant 为知识块 heading。标注由项目构建，尚未经独立人工审核，包含与标题接近的简单查询，不能外推为通用检索质量。
- `repairs.json`：6 个错误初始代码任务，包含开发用例、holdout 和参考实现。参考实现只用于真实 Docker 校验，不作为知识库语料或模型输入。
- 无答案查询单独计算误召回率。BM25 单字中文 token 可能在无关文档上产生弱匹配；此局限必须随报告保留。
- 真实生成 / embedding / rerank 的实验需要显式开关和预算；默认预览不发送请求。

运行命令、模式定义和实验边界见 [RAG_UPGRADE.md](../../docs/RAG_UPGRADE.md)。
