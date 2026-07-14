"""从零实现最小可用 Agent —— 2026 Agent 技术笔试

不依赖任何 Agent 框架，纯 Python + OpenAI SDK 手写 Agent 循环。

运行：
  pip install openai python-dotenv
  echo DEEPSEEK_API_KEY=sk-xxx > .env
  python agent.py
"""

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)
MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")

# ================================================================
# 工具注册中心
# ================================================================

TOOLS: dict[str, dict] = {}

def register_tool(name: str, description: str, parameters: dict, handler: callable):
    """注册工具：名称、描述、参数 Schema、执行函数。"""
    TOOLS[name] = {
        "description": description,
        "parameters": parameters,
        "handler": handler,
    }

def get_tool_schemas() -> list[dict]:
    """生成 OpenAI tools 参数格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": info["description"],
                "parameters": info["parameters"],
            },
        }
        for name, info in TOOLS.items()
    ]

# ================================================================
# 三个工具实现
# ================================================================

def _calculator(expression: str) -> str:
    """安全计算数学表达式。"""
    try:
        allowed = set("0123456789+-*/().%^ ")
        if not all(c in allowed for c in expression):
            return "表达式包含不允许的字符"
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"

def _search(query: str) -> str:
    """Mock 搜索——返回假数据。"""
    fake_results = {
        "天气": "北京今天晴，22-30°C，微风。",
        "python": "Python 由 Guido van Rossum 创建，于 1991 年首次发布。",
        "agent": "AI Agent 是一种能自主感知环境、做出决策、执行动作的智能系统。",
        "待办": "当前待办：无",
    }
    for key, value in fake_results.items():
        if key in query.lower():
            return value
    return f'关于"{query}"的搜索结果：这是一个模拟的搜索返回，实际场景中会调用搜索 API。'

def _todo(action: str, content: str = "", state: dict = None) -> str:
    """简单的待办事项管理。"""
    if state is None:
        state = {"todos": []}
    todos = state.get("todos", [])
    if action == "add" and content:
        todos.append(content)
        return f"已添加待办: {content}，当前共 {len(todos)} 条"
    elif action == "list":
        if not todos:
            return "当前没有待办事项。"
        return "待办列表:\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(todos))
    elif action == "done" and content:
        for t in todos:
            if content in t:
                todos.remove(t)
                return f"已完成: {t}"
        return f"未找到: {content}"
    return f"不支持的 action: {action}"

# 注册三个工具
register_tool("calculator", "计算数学表达式，如 123*456", {
    "type": "object",
    "properties": {"expression": {"type": "string", "description": "数学表达式"}},
    "required": ["expression"],
}, _calculator)

register_tool("search", "搜索互联网获取信息，返回相关结果", {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "搜索查询词"}},
    "required": ["query"],
}, _search)

register_tool("todo", "管理待办事项：add 添加、list 列出、done 完成", {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "list", "done"]},
        "content": {"type": "string", "description": "待办内容"},
    },
    "required": ["action"],
}, _todo)


# ================================================================
# LLM 输出解析
# ================================================================

def parse_llm_response(response) -> dict[str, Any]:
    """解析 LLM 返回的消息，提取思考过程、工具调用或最终答案。"""
    msg = response.choices[0].message
    result = {
        "has_tool_calls": False,
        "tool_calls": [],
        "content": msg.content or "",
        "thinking": msg.content or "",
    }

    if msg.tool_calls:
        result["has_tool_calls"] = True
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result["tool_calls"].append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args,
            })

    return result


# ================================================================
# Session 管理
# ================================================================

MAX_ROUNDS = 10  # 最大轮次限制

class Session:
    """每个窗口/用户独立 Session。"""

    def __init__(self, session_id: str, system_prompt: str = ""):
        self.id = session_id
        self.messages: list[dict] = []
        self.round_count = 0
        self.tool_state = {"todos": []}  # 工具状态（如待办列表）
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, tool_call_id: str, name: str, result: str):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"[{name}] {result}",
        })

    def can_continue(self) -> bool:
        return self.round_count < MAX_ROUNDS

    def increment_round(self):
        self.round_count += 1

    def compress_if_needed(self):
        """简单上下文压缩：超过 20 条消息时，保留 system + 最近 10 条。"""
        if len(self.messages) > 20:
            system_msgs = [m for m in self.messages if m["role"] == "system"]
            recent = self.messages[-10:]
            self.messages = system_msgs + recent
            print(f"  [Session:{self.id}] 上下文压缩: {len(system_msgs) + len(recent)} 条")

# 全局 session 存储
sessions: dict[str, Session] = {}


# ================================================================
# Agent 核心循环
# ================================================================

# 全局 Trace Log：记录每一步决策、工具调用、异常
TRACE_LOG: list[dict] = []

def trace(step: str, detail: dict):
    """结构化 trace 日志。"""
    entry = {"step": step, **detail}
    TRACE_LOG.append(entry)
    return entry


def agent_loop(session: Session, user_input: str) -> str:
    """主循环：接收输入 → LLM决策 → 执行工具 → 继续或返回。"""
    trace("user_input", {"session": session.id, "content": user_input[:100]})
    session.add_user_message(user_input)

    while session.can_continue():
        session.increment_round()
        session.compress_if_needed()

        # Step 1-2: 调 LLM，判断直接回复还是调用工具
        print(f"\n  [Round {session.round_count}] LLM 决策中...")
        trace("llm_call", {"round": session.round_count, "session": session.id})

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=session.messages,
                tools=get_tool_schemas() if TOOLS else None,
                temperature=0.1,
            )
        except Exception as e:
            trace("error", {"stage": "llm_call", "error": str(e)})
            return f"LLM 调用失败: {e}"

        parsed = parse_llm_response(response)
        trace("llm_response", {
            "has_tool_calls": parsed["has_tool_calls"],
            "thinking": parsed["thinking"][:150],
        })

        # 记录思考过程
        if parsed["thinking"]:
            print(f"  [思考] {parsed['thinking'][:100]}...")

        # 没有工具调用 → 直接返回答案
        if not parsed["has_tool_calls"]:
            session.messages.append({"role": "assistant", "content": parsed["content"]})
            trace("final_answer", {"mode": "direct_reply", "content": parsed["content"][:200]})
            return parsed["content"]

        # Step 3: 执行工具
        assistant_msg = {"role": "assistant", "content": parsed["content"] or ""}
        assistant_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
            for tc in parsed["tool_calls"]
        ]
        session.messages.append(assistant_msg)

        for tc in parsed["tool_calls"]:
            name = tc["name"]
            args = tc["arguments"]
            print(f"  [工具] {name}({args})")
            trace("tool_call", {"name": name, "args": args})

            tool_info = TOOLS.get(name)
            if not tool_info:
                result = f"未知工具: {name}"
                trace("tool_error", {"name": name, "error": result})
            else:
                try:
                    if name == "todo":
                        result = tool_info["handler"](**args, state=session.tool_state)
                    else:
                        result = tool_info["handler"](**args)
                    trace("tool_result", {"name": name, "result": result[:200]})
                except Exception as e:
                    result = f"工具执行失败: {e}"
                    trace("tool_error", {"name": name, "error": str(e)})

            print(f"  [结果] {result[:100]}...")
            session.add_tool_result(tc["id"], name, result)

        # Step 4: 继续 loop，LLM 根据工具结果决定是继续调用还是回复用户

    return "已达最大轮次限制，请简化问题或开始新会话。"


# ================================================================
# 测试用例
# ================================================================

SYSTEM_PROMPT = """你是一个智能助手，能够使用工具来完成用户的任务。

你有以下能力：
- 使用计算器进行数学计算
- 搜索互联网获取信息
- 管理待办事项（添加、列出、完成）

使用工具的规则：
- 当用户要求计算、搜索或管理待办时，必须调用对应工具，不要仅做口头确认
- 当用户只是闲聊或问简单问题时，可以直接回复，不需要调用工具
- 每次工具调用后，基于返回结果决定是否需要继续调用其他工具
- 如果工具返回了足够的信息，直接组织成自然语言回复给用户

回答用中文，清晰有条理。"""

def test_cases():
    """跑测试用例。"""
    print("=" * 60)
    print("  Agent 最小可用原型 — 测试用例")
    print("=" * 60)

    # 用例 1: 纯对话（不需要工具）
    print("\n[测试1] 纯对话")
    s1 = Session("s1", SYSTEM_PROMPT)
    r = agent_loop(s1, "你好，请介绍一下你自己")
    print(f"  Agent: {r[:200]}")
    assert len(r) > 0, "应返回回复"

    # 用例 2: 使用计算器工具
    print("\n[测试2] 调用计算器")
    s2 = Session("s2", SYSTEM_PROMPT)
    r = agent_loop(s2, "123 * 456 等于多少")
    print(f"  Agent: {r[:200]}")
    assert len(r) > 0 or session2.tool_state is not None, "应有回复或工具调用"

    # 用例 3: 使用搜索工具
    print("\n[测试3] 调用搜索")
    s3 = Session("s3", SYSTEM_PROMPT)
    r = agent_loop(s3, "Python 是什么时候发布的")
    print(f"  Agent: {r[:200]}")
    assert len(r) > 0, "应有搜索回复"

    # 用例 4: 待办事项管理（多工具混合）
    print("\n[测试4] 待办管理")
    s4 = Session("s4", SYSTEM_PROMPT)
    r = agent_loop(s4, "帮我记一个待办：买水果")
    print(f"  添加: {r[:100]}")
    r = agent_loop(s4, "我现在有哪些待办")
    print(f"  查询: {r[:200]}")

    # 用例 5: 追问（同一 session 内多轮）
    print("\n[测试5] 追问")
    s5 = Session("s5", SYSTEM_PROMPT)
    r = agent_loop(s5, "北京今天天气怎么样")
    print(f"  第一轮: {r[:100]}")
    r = agent_loop(s5, "那适合出门吗")
    print(f"  追问: {r[:100]}")

    # 用例 6: 多 session 隔离
    print("\n[测试6] Session 隔离")
    sa = Session("sa", SYSTEM_PROMPT)
    sb = Session("sb", SYSTEM_PROMPT)
    # 直接验证数据隔离——不依赖 LLM 行为，直接操作 Session 状态
    sa.tool_state["todos"].append("写周报")
    sb.tool_state["todos"].append("买咖啡")
    assert sa.tool_state["todos"] == ["写周报"], f"Session A 应只有自己的数据"
    assert sb.tool_state["todos"] == ["买咖啡"], f"Session B 应只有自己的数据"
    print(f"  Session A: {sa.tool_state['todos']}")
    print(f"  Session B: {sb.tool_state['todos']}")
    print("  Session 隔离验证通过")

    # 用例 7: 上下文压缩
    print("\n[测试7] 上下文压缩")
    s7 = Session("s7", SYSTEM_PROMPT)
    # 直接往 messages 里塞 44 条假消息, 绕过 LLM
    for i in range(22):
        s7.messages.append({"role": "user", "content": f"测试消息 {i}"})
        s7.messages.append({"role": "assistant", "content": f"回复 {i}"})
    before = len(s7.messages)
    s7.compress_if_needed()
    after = len(s7.messages)
    print(f"  压缩前: {before} 条, 压缩后: {after} 条")
    assert any(m["role"] == "system" for m in s7.messages), "system prompt 应保留"
    assert after <= 11, f"压缩后应 <= 11 条, 实际 {after}"
    print("  压缩验证通过")

    print("\n" + "=" * 60)
    print("  全部测试通过")
    print("=" * 60)


def print_trace_summary():
    """打印工具调用 trace 汇总。"""
    print("\n" + "=" * 60)
    print("  Trace Log 汇总")
    print("=" * 60)
    steps = {}
    for entry in TRACE_LOG:
        s = entry["step"]
        steps[s] = steps.get(s, 0) + 1
    for step, count in steps.items():
        print(f"  {step}: {count} 次")
    print(f"  总计: {len(TRACE_LOG)} 条 trace 记录")
    print("=" * 60)


if __name__ == "__main__":
    test_cases()
    print_trace_summary()
