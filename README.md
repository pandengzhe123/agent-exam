# Agent 最小可用原型

2026 Agent 技术笔试 — 从零手写 Agent Runtime，无框架依赖。

## 运行方式

```bash
pip install openai python-dotenv
cp .env.example .env   # 填入 DeepSeek API Key
python agent.py
```

## 系统设计

### Agent 循环

```
用户输入
  → LLM 决策（直接回复 or 调用工具？）
    → 直接回复：返回给用户，结束
    → 调用工具：执行工具 → 结果追加到消息历史 → 回到 LLM 决策
  → 达到最大轮次（10轮）强制返回
```

### 工具注册

每个工具包含：name、description、parameters（JSON Schema）、handler。
LLM 基于 Schema 自主决策调用哪个工具、传什么参数。
扩展新工具只需调用 `register_tool()`。

### Session 管理

用户 A 的窗口 1 和窗口 2 各创建独立的 Session 对象。
每个 Session 持有独立的消息历史、工具状态（如待办列表）。
全局 `sessions` 字典按 session_id 索引。

### Context 管理

#### 最大轮次限制

`MAX_ROUNDS = 10`，每轮 LLM 决策-执行循环计数 +1。达到上限后 Agent 强制停止，返回提示让用户简化问题或开新会话。防止死循环无限消耗 token。

#### 对话记忆与追问

同一 Session 内的 messages 列表**累加不清空**。每轮用户输入以 `role: user` 追加，LLM 回复以 `role: assistant` 追加（含 tool_calls 声明），工具执行结果以 `role: tool` 追加（带 tool_call_id 回指）。下一轮调 LLM 时，完整 messages 作为上下文传入。

**纯对话追问**：用户问 "北京天气怎么样" → Agent 调 search 答了气温 → 用户追问 "那适合出门吗"。此时 messages 包含 `[系统提示, 用户:"北京天气", 工具:trace结果, 助手:"22-30°C", 用户:"那适合出门吗"]`。LLM 看到前一条有 "22-30°C"，自然理解 "那" 指代天气。

**带工具的追问**：用户先 "帮我记待办：买水果"（调了 todo add），再追问 "再加一个买咖啡"（再调 todo add）。此时 messages 包含第一轮的 todo("买水果") 调用记录 + 第二轮的 todo("买咖啡")，Agent 知道之前调过 todo，也看到第一条已经存进列表了。

#### 塞入 Context 的内容

| 消息类型 | 存放内容 | 原因 |
|---------|---------|------|
| system | 角色设定 + 能力说明 | 给 LLM 身份和行为边界 |
| user | 用户每轮输入 | 必须保留——这是 LLM 决策的全部依据 |
| assistant | LLM 回复 + tool_calls 声明 | 记录 LLM 每一步的决策 |
| tool | 工具执行结果（完整文本） | LLM 基于这些结果做下一轮决策；不截断，因为内容本就不长 |

工具执行结果保留完整文本（不做摘要）。因为当前工具返回都很短（几十到几百字），不需要额外处理。如果扩展到网页搜索返回大段内容，则需要截断或 LLM 摘要——这份代码只是原型，不做复杂处理。

#### 基础上下文压缩

`Session.compress_if_needed()`：超过 20 条消息时，保留 system prompt + 最近 10 条消息。20 条阈值确保 Agent 在多轮工具调用后不会无限膨胀消息历史。复杂压缩（LLM 生成摘要替换旧消息）见原项目 DeepResearch Platform 的 `_truncate_context()`，这里不做。



### Memory 召回时机与放置方式

**召回时机**：每次 `agent_loop()` 调 LLM 时，Session 内的完整 `messages` 列表作为上下文传入。不需要额外的"检索"步骤——消息链本身就是记忆。

**放置方式**：记忆以 OpenAI Chat Completion API 标准的 messages 格式存放。四种 role 消息按时间顺序排列：

```
[system]  角色设定（Session 创建时加入）
[user]    用户第 1 轮输入
[assistant] Agent 回复（含 tool_calls 声明）
[tool]    工具执行结果（带 tool_call_id 回指）
[user]    用户第 2 轮追问
...
```

**为什么这样放置**：LLM 原生理解这个格式。不需要自定义记忆格式、不需要额外的检索层。追问时 LLM 看到上一条 `[user]` 和 `[tool]` 的内容，自然理解 "那""它""再加一个" 指代什么。工具调用时 LLM 看到之前的 `[tool]` 结果，知道上次调了什么、为什么调。

**工具状态（tool_state）与消息记忆的关系**：消息记忆存的是"工具返回了什么"，tool_state 存的是"工具内部的数据"。todo 添加待办——tool_state 里多了一条，同时 `[tool]` 消息里也记录了"当前共 N 条"。追问"有哪些待办"——LLM 既可能调 todo list 工具（走 tool_state），也可能直接从历史的 `[tool]` 消息中提取信息（走消息记忆）。两种路径互补。

**压缩对记忆的影响**：`compress_if_needed()` 会丢弃旧的 user/assistant/tool 消息，只保留 system 和最近 10 条。旧消息中如果有未完成的工具调用（孤立的 assistant tool_calls 声明），可能在截断后导致 API 报错。考试原型对此不做复杂处理，完整解决方案见主项目 DeepResearch Platform 的 `_truncate_context()`——该函数用 LLM 将旧对话压缩为摘要后再截断，同时锁死最后 N 条消息保证 tool_calls ↔ tool 响应不被打断。

## 测试用例

| # | 场景 | 验证点 |
|---|------|--------|
| 1 | 纯对话 | 无工具调用时直接回复 |
| 2 | 计算器 | 调 calculator 计算 123*456 |
| 3 | 搜索 | 调 search 查 Python 发布时间 |
| 4 | 待办管理 | 多工具混合：添加+查询待办 |
| 5 | 追问 | 同一 session 内多轮对话，能记住上下文 |
| 6 | Session 隔离 | 两个 session 的待办互不影响（直接验证数据层） |
| 7 | 上下文压缩 | 44 条消息压缩至 11 条，system 保留 |

## AI Prompt 记录

### 1. 系统提示词设计

```text
你是一个智能助手。可以用工具帮助用户完成任务。回答用中文，简洁明了。
```

设计考量：不给过多约束，让 LLM 自由决策。工具调用靠 Schema 描述约束。

### 2. 问题与解决

**问题 1：工具参数 JSON 解析失败**

LLM 偶尔在 arguments 中放入未转义的换行符，json.loads() 报错。
解决方案：catch json.JSONDecodeError 后降级为空 dict，不中断循环。

**问题 2：Session 隔离**

最初用全局 state 字典导致 session 间数据串扰。
解决方案：每个 Session 对象持有独立的 `tool_state`。

**问题 3：上下文膨胀**

多轮工具调用后消息列表快速增长。
解决方案：20 条消息阈值触发截断，保留 system prompt + 最近 10 条。
