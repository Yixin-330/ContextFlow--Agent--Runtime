# ContextFlow 巅峰赛演示资料

MTClaw 的 Function Router 负责根据请求加载和调用匹配工具，并在轻量路径无法完成时回退上游模型。

ContextFlow 位于 MTClaw 前后：它记录用户工作状态，约束当前可调用的 Agent 和工具，生成可审阅工作流，并将执行结果写回评测日志。

巅峰赛演示需要在 MTT AIBOOK 本地运行，覆盖多个垂直工作场景，并让用户以自然语言完成连续请求。
