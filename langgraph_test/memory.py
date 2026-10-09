import os
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import MessagesState, StateGraph, START, END
from langgraph.prebuilt import ToolNode
from dotenv import load_dotenv

load_dotenv()

# 使用内存存储记忆
memory = MemorySaver()

@tool
def search(query: str) -> str:
    """查询指定城市的实时天气
    Args:
        query: 需要查询天气的城市名称
    """
    return f"{query}的天气是暴雨"

tools = [search]
tool_node = ToolNode(tools)
model = ChatDeepSeek(
     model="deepseek-v4-pro",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    api_base=os.getenv("DEEPSEEK_API_BASE")
)
# 将工具绑定到模型上
bind_model = model.bind_tools(tools)

# 条件边
def should_continue(state: MessagesState) -> str:
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        return END
    else:
        return "ACTION"

# 调用模型的函数作为节点
def call_model(state: MessagesState) -> MessagesState:
    response = bind_model.invoke(state["messages"])
    return {"messages": response}

# 画图
workflow = StateGraph(MessagesState)
workflow.add_node("agent", call_model)
workflow.add_node("action", tool_node)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"ACTION": "action", END: END}
)
workflow.add_edge("action", "agent")
graph = workflow.compile(checkpointer=memory)

# 运行
config = {"configurable": {"thread_id": "1"}}

graph.invoke({"messages": [HumanMessage(content="北京的天气怎么样？")]}, config=config)
graph.invoke({"messages": [HumanMessage(content="那适合出门跑步吗？")]}, config=config)
graph.invoke({"messages": [HumanMessage(content="那我应该带什么出门？")]}, config=config)

current_state = graph.get_state(config=config)
result = current_state.values["messages"]
for message in result:
    message.pretty_print()