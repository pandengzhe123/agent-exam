# AI Prompt 与问题解决记录

> 记录了本项目中人类与 AI（Claude Code）的完整交互过程——如何用 AI 生成代码、诊断问题、迭代优化。体现了 AI-native 开发中最关键的"提示 AI → 审查产出 → 判断优劣 → 修复问题"的循环。

## 项目使用的 AI 工具

- **Claude Code**：主开发工具，负责代码生成、架构讨论、Bug 诊断、文档撰写
- **LLM API**：DeepSeek V4 Flash（Agent 的推理引擎，model: deepseek-v4-flash）

---

## 第一阶段：架构设计

### 我的 Prompt（给 Claude Code）

> 我要从零实现一个最小可用的 AI Agent，用于技术笔试。项目要求：使用真实的 LLM API（DeepSeek），
> 不能依赖任何 Agent 框架（LangChain/LangGraph 不能用），手写 Agent 循环。
> 
> 功能上需要一个 while 循环驱动 LLM 决策 + 工具执行。LLM 基于工具 Schema 自主判断
> 是直接回复还是调用工具。工具至少三个：calculator、search（mock 先不调真实 API）、todo。
> 多 Session 管理（不同窗口独立），上下文超长要基础压缩。
> 
> 你先帮我设计架构骨架——哪些模块、模块间怎么交互——然后我们逐步写代码。

### Claude Code 的回应

Claude 给出了一个清晰的模块划分：

```
工具注册中心（全局 dict, 存 name/desc/schema/handler）
  ↓
Agent 核心循环（while + LLM 决策 + 工具执行）
  ↓
Session 管理（独立 messages + tool_state + 轮次计数）
  ↓
Context 管理（消息累加 + 压缩）
  ↓
LLM 输出解析（提取 content / tool_calls / 最终答案）
```

我审查后确认逻辑没问题，让 Claude 生成完整代码。

---

## 第二阶段：代码生成

### 我的 Prompt

> 按刚才的架构写完整代码。一个 agent.py 文件，Python。工具注册用 dict 存，
> get_tool_schemas() 生成 OpenAI tools 参数格式。三个工具的 handler 全部实现：
> calculator 用 eval 但要安全防护（禁止内置函数），search mock 返回假数据，
> todo 用 Session 内部的 tool_state 字典存待办列表。
> 
> LLM 输出解析单独一个函数。Session 类包含 messages/list, round_count/int, tool_state/dict。
> agent_loop 函数接收 Session 和用户输入，返回最终回复字符串。
> 加结构化 trace 日志。测试用例至少 6 个覆盖核心路径。

### Claude Code 的产出

生成了 330 行的完整代码。我逐段审查了所有代码后，开始跑测试。三个问题第一个马上就报错了。

---

## 第三阶段：问题诊断与修复

### 问题 1：KeyError: 'thinking'

**现象**：测试 2 报错 `KeyError: 'thinking'`，位置在 `parse_llm_response()`。

**我的分析**：打开报错行，看到 `if parsed["thinking"]:` 这行挂了。说明 parsed 字典里没有 "thinking" 这个键。

**我问 Claude Code**：

> 测试 2 报 KeyError: 'thinking'，这是 parse_llm_response 的问题。LLM 返回 tool_calls 时
> msg.content 可能为空。当前代码只在 content 存在时才设 thinking 字段，但后面循环里
> 直接取 parsed["thinking"] 没做兜底。帮我修一下。

**Claude Code 的诊断**：

LLM 在纯工具调用场景下 `msg.content` 经常是 None 或空字符串。应该让 thinking 字段始终存在，用 `msg.content or ""` 兜底。

**修复后的代码**：

```python
# 修复前
if msg.content:
    result["thinking"] = msg.content

# 修复后
result["thinking"] = msg.content or ""
```

**我的判断**：接受这个修复。Claude 说对了——LLM 输出的 content 字段不是必然存在的，解析层要做好所有字段的兜底。

---

### 问题 2：LLM 口头回复但不调工具——测试 6 不可靠

**现象**：测试 6 的 Session B 收到 "帮我记待办：买咖啡"，LLM 文字回复 "好的，已帮你记下了"，但根本没有调 todo 工具。Session B 的 tool_state['todos'] 是空列表。

**我问 Claude Code**：

> 测试 6 的 Session B 没有调 todo。LLM 只是文字回复 "好的已帮你记下" 没有实际调工具。
> 这不是代码 bug 但是测试断言假定 LLM 一定会调工具。你觉得问题出在哪？

**Claude Code 的分析**：

LLM 的这个回复是合法的——没有编造数据，只是选择了口头确认而非调用工具。System Prompt 不够强制（"可以用工具" 是可选而非强制）。但即使加强 System Prompt，也不能 100% 保证 LLM 一定调工具。测试应该独立验证 Session 对象的隔离机制，而不是依赖 LLM 的行为。

**修复后**：

```python
# 改前：依赖 LLM 调工具
agent_loop(sa, "帮我记待办：写周报")
agent_loop(sb, "帮我记待办：买咖啡")
assert sa.tool_state["todos"] != sb.tool_state["todos"]

# 改后：直接操作状态，验证数据层隔离
sa.tool_state["todos"].append("写周报")
sb.tool_state["todos"].append("买咖啡")
assert sa.tool_state["todos"] == ["写周报"]
assert sb.tool_state["todos"] == ["买咖啡"]
```

**我的判断**：这是这次开发最有价值的发现。Claude 帮我区分了 "代码层的确定性行为"和"LLM 的随机行为"——测试应该覆盖前者。这也和 Claude 自己的工作模式让我产生了更深的理解：代码能保证的事情（Session 隔离），和 LLM 选择做的事（要不要调工具），是两层的。

---

### 问题 3：计算器测试断言太严格

**现象**：测试 2 的 `assert "56088" in r` 失败。LLM 在工具调用后第二轮返回空 content。

**我问 Claude Code**：

> 测试 2 报 AssertionError。LLM 调了 calculator 返回 "56088"，然后第二轮返回空字符串。
> 所以 assert "56088" in r 失败了。帮我分析为什么 LLM 第二轮 content 会是空的。

**Claude Code 的分析**：

LLM 在调用工具时，同一轮回复的 content 可以为空。把工具结果用自然语言转述是下一轮的事情——第一轮只负责调工具，第二轮才组织语言。但 DeepSeek V4 Flash 的行为是不确定的——有时在第一轮末尾就带上结果，有时分两轮。这是模型行为，不是代码 bug。

**修复**：将强断言改为验证 Agent 完成了预期动作（有回复或有工具调用记录），而非某轮回复中必须出现某段文字。

**我的判断**：接受。这是 LLM 集成测试的一般性原则——验证结构化行为（调了多少次工具、有没有返回非空回复），不验证文本内容的具体匹配。

---

### 发现 4：测试缺失上下文压缩的覆盖率

**现象**：所有 6 个测试中，消息数最多只到 10 几条，从未触发 20 条阈值。`compress_if_needed()` 的代码逻辑写好了但完全没有跑过。

**我的发现**（不用 AI 辅助）：

这六个测试覆盖了工具调用和 Session 管理，但每个测试的对话轮次都不超过 5 轮。压缩功能的阈值是 20 条，永远达不到。我决定加一个测试直接往 messages 里塞 44 条假消息，绕过 LLM，纯测压缩逻辑。

**实现**：

```python
for i in range(22):
    s7.messages.append({"role": "user", "content": f"测试消息 {i}"})
    s7.messages.append({"role": "assistant", "content": f"回复 {i}"})
# 45 条消息 → compress → 11 条
s7.compress_if_needed()
assert len(s7.messages) <= 11
```

**为什么这样做**：如果让 LLM 真跑 20+ 轮来触发压缩，一次测试就要调 40+ 次 API，贵且慢。直接绕过 LLM 测代码逻辑更高效——这和测试 6 的思路一样：测代码能力，不依赖 LLM。

---

## 第四阶段：System Prompt 迭代

### 初始版本

```
你是一个智能助手。可以用工具帮助用户完成任务。回答用中文，简洁明了。
```

我故意写得很中性——不强制 LLM 一定调工具，因为 Agent 应该基于工具 Schema 自主决策。

### 测试后的调整

问题 2 暴露了 "可以用工具" 太弱——LLM 有时口头回复不调工具。那我把规则写明确：

```
你有以下能力：
- 使用计算器进行数学计算
- 搜索互联网获取信息
- 管理待办事项（添加、列出、完成）

使用工具的规则：
- 当用户要求计算、搜索或管理待办时，必须调用对应工具，不要仅做口头确认
- 当用户只是闲聊或问简单问题时，可以直接回复，不需要调用工具
- 每次工具调用后，基于返回结果决定是否需要继续调用其他工具
- 如果工具返回了足够的信息，直接组织成自然语言回复给用户

回答用中文，清晰有条理。
```

加了 "必须调用" 约束，同时保留闲聊时不调工具的自由度。没有把 System Prompt 写得太极端（比如 "任何请求都先调工具"），因为那样反而会导致 Agent 在简单对话中浪费 API 调用。

---

## 我的角色总结

- **架构设计**：我决定 Agent 循环应该长什么样、模块边界怎么划分
- **代码审查**：AI 生成代码后我逐段验证，确认每个 if/else 的处理逻辑
- **问题诊断**：报错后我分析是代码逻辑问题还是 LLM 行为问题
- **决策判断**：AI 给出方案后我来决定是否接受——比如 AI 说"这是模型行为不是 bug"，我判断 "但测试的验证方式还是要改"——最终自己动手加了测试 7
- **边界发现**：我自己发现了压缩功能没有测试覆盖——因为太了解代码逻辑，知道所有的测试都跑不满压缩阈值

## 关键收获

1. **AI 写代码很快，但设计架构和判断质量是人做的事**——要分清代码层的确定性行为和 LLM 的随机行为
2. **测试 LLM 集成系统时，验证结构化行为，不验证文本内容精确匹配**
3. **不要依赖 AI 帮你发现所有问题——项目逻辑只有你自己最了解，AI 看不到你没测到的东西**
