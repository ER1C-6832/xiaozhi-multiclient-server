# LLM Token 统计日志 Phase 1 交付报告

## 范围

第一阶段在 OpenAI 兼容流式适配器和连接层实现，不绑定具体模型名称。当前支持：

- 普通文本回复；
- Function Calling 回复；
- 工具执行结果回填后的递归 LLM 请求；
- 供应商返回的输入、输出、总 Token；
- 工具 Schema 数量与字符数；
- 各角色消息的序列化字符数；
- 本地工具参数/结果字符数和执行耗时；
- 同一用户轮次内多次 LLM 请求的累计汇总。

本阶段只观测，不执行 Token 限制。

## 隐私边界

日志不保存以下内容：

- API Key；
- system prompt 正文；
- 用户消息正文；
- 工具 Schema 正文；
- 工具参数正文；
- 工具结果正文。

只记录模型名、关联 ID、计数、字符数、耗时、工具名、动作类型和供应商 usage。

## 日志事件

所有结构化事件均以 `TOKEN_USAGE ` 开头：

- `llm_usage`：单次模型 API 请求；
- `tool_usage`：单次本地工具执行；
- `turn_usage_summary`：一次用户轮次最终汇总。

OpenAI 兼容流式请求使用：

```json
{"stream_options":{"include_usage":true}}
```

适配器兼容 `prompt_tokens/completion_tokens` 和
`input_tokens/output_tokens` 两类字段命名。供应商未返回 usage 时记录
`usage_unavailable`，不会伪造 Token 数字。

## 自动验证

- Python 编译检查通过；
- 新增 usage/流式适配器测试 8 项；
- 连同传输策略和 B2 ASR 回归共 14 项测试全部通过；
- Docker 服务器镜像构建通过；
- GLM OpenAI 兼容接口真实流式 usage 验证通过。

## 真实工具轮次证据（2026-07-27）

输入：`现在几点？请只回复当前时间。`

| 阶段 | 输入 Token | 输出 Token | 总 Token | 工具数 | Schema 字符 |
|---|---:|---:|---:|---:|---:|
| 初次请求 | 1405 | 10 | 1415 | 6 | 2082 |
| 工具回填请求 | 8689 | 7 | 8696 | 37 | 12178 |
| 整轮 | 10094 | 17 | 10111 | — | — |

模型选择了只读工具 `get_lunar`：

- 参数：12 字符；
- 结果：628 字符；
- 本地执行：1 ms；
- 动作：`REQLLM`，因此触发第二次模型请求。

该证据确认此前“一句话消耗近万 Token”的主因不是输出，而是工具结果回填后，
再次发送完整历史和 37 个工具 Schema。下一阶段应优先实现工具集合路由，而不是
只降低 `max_tokens`。

## 查看方法

```bash
docker logs -f xiaozhi-esp32-server 2>&1 | grep TOKEN_USAGE
```

只看整轮：

```bash
docker logs xiaozhi-esp32-server 2>&1 \
  | grep 'TOKEN_USAGE.*turn_usage_summary'
```
