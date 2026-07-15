# 架构设计题回答

---

## 模块一第 2 题：200 轮 context 压缩

我在自己项目里做过三层压缩。第一层预警——上下文超过 80% 时通知用户"快满了，建议开新会话"。第二层 LLM 压缩——超限时取中间要被丢弃的消息发给 LLM，让 LLM 生成摘要保留关键事实、用户偏好和约束条件，摘要以 system 消息插入。这比裸截断可靠——简单截断会丢掉"用户预算 5000 元"这类关键约束。第三层硬截断兜底——压缩 LLM 调用本身也可能失败，这时保留第一条消息（messages[0] 锚定原始问题）+ 最近五条，保证 Agent 至少记得研究目标。
对于 200 轮的场景，分层策略更有效：近期 5 轮完整保留保证细腻度，中期 5-20 轮用 LLM 压缩成摘要，远期 20-200 轮只保留结构化信息（结论、关键决策、用户偏好）。流畅性的关键在于——摘要中保留用户的关键约束和偏好、保留每次重大决策的上下文（为什么上次选了 A 而非 B）、以及任何未完成的任务。

---

## 模块二第 2 题：Agent Memory 经典框架

我理解的经典框架是三层记忆。工作记忆（Working Memory）：当前对话上下文窗口，LLM 直接读取。我在项目里用 messages 列表承载——system/user/assistant/tool 四种 role 顺序排列。超限后走渐进压缩——80% 预警，LLM 将旧对话压缩为摘要，硬截断兜底保留首条锚点+最近五条。
短期记忆（Short-term）：PostgreSQL JSONB 存完整对话历史，每条包含 role、content、time。通过 appendHistory() 追加不覆盖，多轮追问时通过 getContextHistory() 取出格式化为 LLM 可读的文本注入上下文。超过 40 条消息自动触发 LLM 压缩。report JSONB 数组存所有历史报告——去重后追加兜底，history 截断时报告通过指纹搜重补回。
长期记忆（Long-term）：RAG 知识库——Chrom 向量化存储用户上传的私有文档，Agent 通过 search_kb 工具按需检索。每个用户独立的 Collection，物理隔离。hybrid 模式下做了首轮 KB 预搜——Agent 启动时先用问题搜一次知识库，有结果以 system 消息注入，LLM 读到内容后自行判断是否深挖。
头部玩家正在探索 Agentic Memory——不再由开发者定义"什么该记"，而是 Agent 自己在对话中判断信息重要性，自主决定存储和召回。代表性产品如 Mem0、Letta。发展趋势目前看来是从被动加载到主动管理。

---

## 模块三第 1 题：长程任务忘目标

我了解四种常用方案，各有优缺，我项目里实践了其中两种。
我在 L4 Supervisor 中处理的方式是 Research Brief——任务开始前把用户模糊问题扩写成结构化简报，明确核心问题、需要覆盖的维度和优先级。这份简报贯穿整个 Supervisor 决策过程，每一步都以它为准绳。
另一个我用了的是锚点法——messages[0] 永远锁死，不管截断多少对话。成本为零，但缺点是信息量有限，只有一句原始问题，用户隐含的偏好它覆盖不了。
我没做但了解的两种：子目标分解，Claude Code 和 OpenCode 用的——复杂任务拆成小步骤逐个执行验证，优点是每步可独立检查，缺点是分解本身依赖 LLM 质量，分得不好反增出错。检查点回滚，Devin 的做法——长任务中途保存快照，偏了就回滚。有纠错但增加了 token 消耗和复杂度。还有就是外部监督 Agent，等于我做过的 L4 Supervisor，独立 Agent 定期审查执行方向和维度覆盖。

---

## 模块四第 2 题：session busy 冲突

这个要先分清两种冲突，处理方式不一样。
用户发新消息是改主意了，他想做的事变了。这种不该排队，旧任务跑完用户已经不想等了。我在项目里做的方案是抢占取消：前端"停止"按钮 → AbortController 断连接 → Python 的 task.cancel → CancelledError 穿透所有 await → LLM 和搜索全停。跑完的部分先保存，前端展示"研究已停止"。
异步工具结果是旧任务在任务已被取消后才回来。这时候先看当前 session 是不是 busy——不 busy 就追上文的上下文，busy 就排队，不丢弃但也不强插。
而且要注意的点是，Agent 的上下文不是随便加一条消息进去就算的，这个坑我踩过。OpenAI 协议要求 tool_calls 里的 id 和后面 tool 消息的 tool_call_id 一一对应。如果 Agent 正在跑的时候从外部塞入消息，这个链就断了。对我来说最简单的方案就是前端锁输入框——session busy 的时候用户不能发消息。这么做的多等几秒不重要，Agent 不会在坏掉的状态下继续做事。

---

## 模块五第 1 题：Claude Code vs Function Calling

我手写过原生 OpenAI Function Calling，对两者的差异有直接体会。
OpenAI Function Calling：声明式。调用前在 tools 参数中声明工具 Schema。LLM 返回 structured tool_calls 数组，每条有 id、function.name、function.arguments JSON 字符串。开发者解析执行后以 role: tool 回传结果。工具调用和文本回复是互斥的——LLM 要么输出文字，要么输出 tool_calls，不能同时。协议要求明确——N 条 tool_calls 对应 N 条 tool 响应，id 必须原样回传。
Claude Code：工具调用是对话的一部分。Claude 在思考过程中用结构化标记表示想调哪个工具，然后在同一回复中继续分析和使用结果。调用和推理是混合的——不需要像 OpenAI 那样"调用就停，等结果再继续"。
国内模型如 GLM、豆包基本走 OpenAI 兼容路线，function calling 格式跟 OpenAI 一致——也是 tools 参数声明 Schema、tool_calls 数组返回、role: tool 回传。但细微差异存在——参数名称的拼写规范、错误返回的 HTTP 状态码偏好、JSON Schema 里 required 字段的严格程度。接入时需要做兼容适配。
总结：OpenAI 的 Schema 强约束适合生产环境——参数类型验证严格，多工具并行调用做得好。Claude 对交互式编程友好——开发者能看到推理过程，但格式一致性靠 prompt 而非 Schema。国内模型走兼容路线省了入门的成本，但细节差异要逐一适配。

