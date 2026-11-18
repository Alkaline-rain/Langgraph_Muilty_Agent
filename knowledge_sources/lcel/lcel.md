# 离线 LCEL 使用笔记

本笔记由项目维护，针对沙箱锁定的 langchain-core 1.6.6；不是官方文档的完整复制。示例仅使用离线组件。来源为已安装包的公开接口和项目离线执行协议。

## ChatPromptTemplate 字典输入

ChatPromptTemplate.from_template 使用模板变量名作为 invoke 输入字典的键。模板 "{text}" 对应 {"text": "..."}；把键写成 "question" 不会自动映射为 text。应核对 input_variables 和调用处字段。

```python
prompt = ChatPromptTemplate.from_template("{text}")
value = prompt.invoke({"text": "hello"})
```

## 模板变量与字面花括号

模板文本中的花括号可能被解释为变量。需要表达 JSON 字面对象时，对作为字面的花括号使用双花括号。只转义字面部分，保留需要替换的变量。

```python
prompt = ChatPromptTemplate.from_template('{{"answer": "{text}"}}')
```

## 多消息模板

ChatPromptTemplate.from_messages 接受角色和模板的列表。用户数据通过变量传入；不要把候选执行权限写在用户消息里并期待其改变宿主限制。

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "Return a short answer."),
    ("human", "{text}"),
])
```

## PromptValue 不是普通字符串

prompt.invoke 返回 PromptValue，而非最终模型输出。需要字符串时可以使用 to_string；需要运行链时把 PromptValue 交给下游模型。不要直接把 PromptValue 当作 JSON 返回。

## LCEL 管道运算符

LCEL 使用 | 将 Runnable 或可适配对象组合。每一步输出成为下一步输入。只检查组件类型还不够，应同时检查实际输入输出的形状。

```python
chain = prompt | model | StrOutputParser()
```

## invoke 同步调用

当前候选入口是同步 solve(payload)，使用 chain.invoke(input) 获取结果。定义链并不会执行链。不要在此同步入口直接返回 coroutine 或调用未等待的 ainvoke。

## batch 批量调用

Runnable.batch 接收输入列表并返回输出列表。每个输入必须符合链入口协议。若需求只处理一个 payload，使用 invoke，避免无意把字典迭代为键。

## RunnableLambda 包装函数

RunnableLambda 将 Python callable 包装为 Runnable。函数参数接收上一阶段的实际输出。不要把返回值预先计算后传给 RunnableLambda。

```python
step = RunnableLambda(lambda value: value.upper())
```

## RunnableLambda 字典输入

如果上游提供字典，lambda 应读取相应字段；不能直接对字典调用字符串方法。可先用一个步骤抽取字段，再交给字符串处理步骤。

```python
step = RunnableLambda(lambda data: data["text"].upper())
```

## 链中的输入映射

上游 payload 的字段名可能与 prompt 变量不同。应在进入 prompt 前明确映射，而不是修改与用户约定的输入协议。

```python
map_input = RunnableLambda(lambda payload: {"text": payload["question"]})
chain = map_input | prompt
```

## RunnableParallel 分支输入

RunnableParallel 的各分支接收相同的原始输入，返回以分支名称为键的字典。不是前一个分支输出传给后一个分支，串行依赖应使用管道。

## RunnablePassthrough 保留值

RunnablePassthrough 将输入传递下去。输入为字典时可在后续映射中保留已有字段；不要假定一个普通字符串已经包含可索引的命名字段。

## FakeListLLM 离线响应

FakeListLLM 用预先提供的 responses 模拟模型返回，不会访问外部服务。它适合验证链的数据流，不代表真实模型会遵循 prompt 内容。

```python
from langchain_core.language_models.fake import FakeListLLM
model = FakeListLLM(responses=["OK"])
```

## FakeListLLM 与输入语义

FakeListLLM 的返回取决于 responses 配置，不会根据提示词真实推理。若任务要求返回 payload.response，应由任务输入构造相应 responses，不能期待修改 prompt 就改变固定回答。

## StrOutputParser 输出

StrOutputParser 将兼容模型输出解析为文本，不负责把 JSON 字符串转为字典，也不自动提取文本中的业务字段。

```python
chain = prompt | model | StrOutputParser()
text = chain.invoke({"text": "hello"})
```

## JSON 字符串与 JSON 对象

字符串 '{"answer": 1}' 和字典 {"answer": 1} 是不同返回类型。若要求对象，可使用 json.loads 解析模型文本，再由 solve 返回字典。不要重复 json.dumps 导致双重编码。

## 输出协议与调试打印

沙箱 runner 负责将 solve 的返回值序列化，并把候选 print 重定向至 stderr。打印不是返回结果：只 print 不 return 会使返回值为 None，序列化为 null。过量调试输出仍可能触发输出限制。

## 导入路径

本镜像使用 langchain_core。ChatPromptTemplate 位于 langchain_core.prompts，StrOutputParser 位于 langchain_core.output_parsers，RunnableLambda 位于 langchain_core.runnables。不要替换为镜像未安装的顶层 langchain 包。

## 禁网执行与模型客户端

候选容器禁网且无模型密钥。不得在候选中初始化 ChatOpenAI 或 ChatDeepSeek 访问服务。宿主生成模型与候选内部的离线模拟模型是不同环节。

## 错误分类与验证

缺少模板变量属于输入协议问题；对错误类型对象调用方法属于数据形状问题；未安装模块属于环境兼容问题。文档只能帮助定位，候选修改后仍需可信用例验收；静态检查通过不证明功能正确。
