from langchain_core.messages import AnyMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph
from typing_extensions import TypedDict
from IPython.display import display


# 定义节点通讯的消息格式
class State(TypedDict):
    messages: list[AnyMessage]
    extra_field: int

# 定义节点
def node(state: State) -> State:
    messages = state["messages"]
    new_message = AIMessage("你好，我是节点1")
    return {
        "messages": messages + [new_message],
        "extra_field": 1
    }

# 创建图
graph = StateGraph(State)
# 添加节点
graph.add_node(node)
# 设置入口节点
graph.set_entry_point("node")
graph_builder = graph.compile()
# 调用
result = graph_builder.invoke({
    "messages": [
        HumanMessage("你好")
    ]
})
for message in result["messages"]:
    message.pretty_print()