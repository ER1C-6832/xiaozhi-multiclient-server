# 自部署语音助手阶段 Checkpoint

- **日期**：2026-08-04
- **更新时间**：2026-08-04 18:11（UTC+8）
- **范围**：服务端 `ER1C-6832/xiaozhi-multiclient-server` 与 PC 客户端 `ER1C-6832/note-assistant-app`
- **服务端开发分支**：`feature/asr-sherpa-sensevoice-b2`
- **服务端远程 HEAD**：`997ab96604d0812fc7775a6ae247f40f10dcc3dd`（`new probe`）
- **客户端开发分支**：`rewrite/single-process-runtime`
- **客户端远程 HEAD**：`429520d6f66564cbe6c84ad9b42a313620ad6d7a`（`fallback fix`）
- **用途**：记录当前相对上游/旧主线的功能差异、已验证能力、部署边界、已知风险和下一阶段入口，作为后续客户端 TTS 生命周期验收、Edge TTS 延迟优化、ASR/TTS 演进、服务端 MCP 编排和客户端收薄工作的恢复点。

---

## 1. 当前阶段结论

当前系统已经从“官方服务 + 旧多进程 PC 客户端”演进为一套可实际运行、可诊断、可回滚的自部署链路：

1. 服务端已建立本地 Sherpa-ONNX + SenseVoice B2 ASR 工程基线，完整话语识别延迟和准确性已达到当前阶段可用水平。
2. 服务端已加入 Token usage 统计、chat/tool 分层预算、工具候选裁剪、结构闸门、确定性路由、durable workflow、参数归一化和工具执行真实性保护。
3. PC 客户端已重写为单进程 Runtime，并在本地执行便签、标签、UI、确认、音频与 MCP 能力。
4. 客户端已补齐泛化查询失败关闭、数字标题解析、确认事务闭环、会话生命周期监督和播放断流自动恢复。
5. 服务端当前 Edge TTS 保持“稳定完整 MP3 收集后再解码”的正确性路径；历史上失败的低延迟自制流式转码没有恢复。
6. 服务端已加入可选的 LLM→TTS→Opus→WebSocket 全链路计时、日志汇总分析器和本地 PCM→Opus→发送探针。
7. 实测已经确认：普通聊天首音频延迟中，Edge TTS 路径占比高于 LLM；更换为千问 `qwen3.7-plus` 后主结论不变。
8. 本地 PCM→Opus→WebSocket 发送本身很快，不是当前秒级延迟主因；主要延迟来自 Edge 请求到首个 MP3 chunk，以及收到首 chunk 后仍等待完整 MP3。
9. 客户端曾使用 6 秒 `tts_state_fallback` 提前完成回合。当 Edge 首音频超过该阈值时，晚到的 `sentence_start` 和 Opus 已失去有效 turn token，造成“服务端发送、客户端收包、播放器不启动”的吞音。
10. 客户端最新提交已删除短 6 秒 fallback，改为 TTS 生命周期所有权：非终止 TTS 状态保留回合，直到明确 terminal、abort、断线或 60 秒异常生命周期 watchdog。
11. 当前优先级已经调整为：先完成客户端 delayed-TTS 生命周期真实验收；确认不再吞晚到音频后，再优化 Edge TTS 或替换为原生流式 Provider。

当前仍不建议大规模重构客户端 MCP。客户端继续冻结为“本地能力执行端”，服务端继续承担意图、workflow、工具规划、预算和真实性判断。

## 2. Git 分支快照

### 2.1 服务端

GitHub 对比 `main...feature/asr-sherpa-sensevoice-b2` 的最新阶段快照：

- 状态：`diverged`
- feature 相对 main：**ahead 28 commits**
- feature 相对 main：**behind 5 commits**
- 当前 feature HEAD：`997ab96604d0812fc7775a6ae247f40f10dcc3dd`
- HEAD 提交：`new probe`
- 对比时 main 基准提交：`50895d6e332436b37d0fd8e08268b9a2617c5cbe`
- merge base：`de45f73efdd24e9343427a56b5d22f857b6bb7a7`

这意味着 feature 不是简单附着在最新 main 上。后续同步上游时，必须重点检查：

- `core/connection.py`
- `core/providers/tools/`
- `core/providers/llm/`
- `core/providers/tts/`
- `core/handle/sendAudioHandle.py`
- `core/utils/tts_latency_trace.py`
- `config.yaml`
- `manager-api` 的 Liquibase 迁移

不要直接用上游版本覆盖这些文件。

当前远程 HEAD 还包含一个不应进入正式源码的备份文件：

```text
main/xiaozhi-server/tools/tts_pcm_pipeline_probe.py.before-probe-fix1-20260804162726
```

它不影响运行，但应在下一次服务端提交中删除，避免把本地修复备份长期留在仓库。

### 2.2 PC 客户端

GitHub 对比 `master...rewrite/single-process-runtime` 的最新阶段快照：

- 状态：`ahead`
- rewrite 分支相对 master：**ahead 94 commits**
- behind：`0`
- 当前 rewrite HEAD：`429520d6f66564cbe6c84ad9b42a313620ad6d7a`
- HEAD 提交：`fallback fix`
- merge base / master 基准：`1917512f3b06112a6f1453e4110209c5f6de81b9`

客户端 rewrite 已经不是局部补丁，而是完整单进程 Runtime 主线。后续不要尝试把旧 `master` 的 sidecar、多进程服务或旧 controller 结构整体合回来。

最新提交只针对 TTS 回合生命周期：

```text
pc-app-build/apps/notes-pyside/app/assistant/network/websocket_transport.py
pc-app-build/tests/test_tts_lifecycle_fallback.py
```

### 2.3 每次进入下一阶段前记录精确版本

在两个本地仓库分别执行：

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

部署服务器再记录：

```bash
sudo docker inspect xiaozhi-esp32-server --format '{{.Config.Image}}'
sudo docker image inspect "$(sudo docker inspect xiaozhi-esp32-server --format '{{.Config.Image}}')" --format '{{.Id}} {{.Created}}'
```

将结果补入本文件或单独的发布记录，避免只依赖镜像标签判断代码版本。

## 3. 服务端相对上游 main 的核心增强

## 3.1 ASR B2：Sherpa-ONNX + SenseVoice 本地增强

新增 Provider：

```text
main/xiaozhi-server/core/providers/asr/sherpa_sensevoice_b2.py
```

工程组合：

- Sherpa-ONNX `OfflineRecognizer`
- SenseVoice FP32 ONNX
- 固定中文与 ITN
- 4 个推理线程
- 每个完整话语创建独立 Silero VAD 状态
- 尾静音约 0.5 秒
- 前后边界补偿约 320 / 240 ms
- 相邻片段补偿不超过静音间隔的一半
- 小于 0.3 秒的片段过滤
- VAD 未命中时回退完整话语，避免整句直接丢失
- 输入电平诊断与真实链路日志

配套交付：

- `tests/test_sherpa_sensevoice_b2.py`
- `docs/sherpa-sensevoice-b2.md`
- `docs/report/ASR_B2_SHERPA_SENSEVOICE_DELIVERY_REPORT.md`
- 管理端 Provider / Model / 系统模板 Liquibase 迁移
- FunASR 继续保留为可回滚项

已记录的阶段表现：

- 离线单句推理约 0.16–0.30 秒，不含首次模型加载
- 在线真实链路约 0.121–0.237 秒
- 近中场效果可用
- 远场相对默认 FunASR 更能维持中文和时间实体，但仍有替换、缺词

边界：

- 当前仍是完整话语后的离线识别，不提供实时 partial transcript。
- B2 的定位是本地可回滚基线，不是最终流式 ASR。
- 模型权重不入 Git，部署必须挂载：
  - `model.onnx`
  - `tokens.txt`
  - `silero_vad.onnx`

## 3.2 LLM usage 统计与 Token 预算机制

新增或重点修改：

```text
core/providers/llm/usage.py
core/providers/llm/openai/client_config.py
core/connection.py
core/providers/llm/*
config.yaml
```

主要能力：

- 记录 Provider 实际返回的 input/output/total/cached/reasoning usage。
- 每轮记录 LLM 调用次数、工具调用次数、工具 follow-up、参数与结果字符数、总耗时。
- 记录工具 Schema、系统提示、用户、助手、工具结果等消息字符构成。
- 请求前做结构闸门，不把字符估算伪装成精确 tokenizer。
- usage 通过结构化日志与客户端 Developer 诊断暴露。
- OpenAI 兼容客户端增加显式 timeout / retry 配置，避免无效凭据或上游故障卡住几十秒。

当前 feature `config.yaml` 的实际默认值：

| 配置 | chat | tool |
|---|---:|---:|
| 每轮总 Token | 2000 | 6000 |
| 每轮 LLM 调用 | 1 | 4 |
| 每轮工具执行 | 0 | 6 |
| 单次输出 Token | 80 | 120 |
| 单次工具数 | 0 | 5 |
| 单次工具 Schema 字符 | 0 | 5000 |
| 单次消息字符 | 5000 | 9000 |

本地路由默认：

```text
max_candidates = 3
max_tool_schema_chars = 5000
compact_base_prompt_max_chars = 2400
max_history_messages = 6
max_history_chars = 2000
max_tool_result_chars = 3000
```

注意：早期 `LLM_TOKEN_BUDGET_LAYERED_ROUTING.md` 报告中的 tool 调用上限为 2 次 LLM / 3 次工具；后续 durable workflow 强化后，当前源码配置已经扩展为 4 / 6。以后以实际配置和运行日志为准。

真实收益基线：

- 原始普通“你好”曾携带约 38 个工具、12423 字符 Schema，总输入约 8253 Token。
- chat 无工具快路径后，同口径约 451–470 输入 Token，总量下降约 94%。

设计边界：

- chat profile 明确不携带工具。
- tool profile 允许小候选集合与多步 workflow。
- Token 预算是成本和异常循环保护，不应替代业务状态机。
- 路由误判会导致 chat profile 工具数为 0，因此 durable workflow 与路由正确性是强依赖。

## 3.3 工具路由、参数归一化与 durable workflow

新增：

```text
core/providers/tools/tool_routing.py
core/providers/tools/tool_workflow.py
core/providers/tools/tool_arguments.py
```

大幅修改：

```text
core/connection.py
core/providers/tools/device_mcp/mcp_executor.py
core/providers/tools/device_mcp/mcp_handler.py
core/providers/tools/device_mcp/mcp_client.py
```

目标：避免每轮把全部工具交给模型，同时解决短口语、多轮补参数和写操作真实性问题。

已实现：

- 根据领域与动作做本地确定性候选选择，不额外调用云模型。
- 泛化“查一条便签”不会随意选一个关键词执行，进入澄清 workflow。
- 有明确语义目标的查询可以直接选择 `notes_search`。
- “最近几条”使用最近列表，而不是全文搜索。
- 新增、查找、读取、打开、修改、删除、恢复、标签和页面导航均可建立持久 workflow。
- 支持短口语“改一条”“删一条”等操作识别。
- 通用 edit workflow 可继续细化为标题、正文替换、追加、标签、置顶等动作。
- 参数补充轮即使只有数字、正文或标签，也继续使用 tool profile。
- 工具失败、未找到、候选歧义或缺少参数时保留 workflow，不退化成普通聊天。
- “标题是 3”归一化为 `exact_title="3"`，不会把用户可见数字当数据库 ID。
- “内容叫……”抽取语义正文关键词。
- 标签页、回收站、置顶、待办、全部列表使用明确 UI 工具候选。
- 确认与取消也是 durable tool workflow。

## 3.4 工具执行真实性保护

早期问题：路由一旦误判为 chat，模型会在没有任何 MCP 调用时声称“已修改”“已删除”。

当前强化方向：

- 成功声明必须有真实 mutation tool outcome 支撑。
- `notes.resolve` 只代表定位目标，不代表修改或删除完成。
- 高风险工具返回 `requires_confirmation` 时不能宣称已完成。
- 普通聊天路径也需要拦截未验证的写操作成功话术。
- 工具 follow-up 使用有界结构化结果，不把无限原始数据重新送入 LLM。
- request ID、执行结果和工具报告做关联，降低重复或串线风险。

相关测试：

```text
test_device_mcp_execution_truth.py
test_device_mcp_request_ids.py
test_tool_routing*.py
test_tool_workflow*.py
test_tool_argument_normalization.py
test_connection_workflow_source_contract.py
```

## 3.5 TTS 当前稳定路径

当前 `feature` 的：

```text
core/providers/tts/edge.py
```

在音频行为上仍采用稳定的完整 MP3 路径：

```text
第一分句形成
→ Edge `communicate.stream()` 返回 MP3 chunks
→ 服务端把全部 chunks 收集成完整 MP3
→ 完整 MP3 解码 / 重采样为 PCM
→ PCM 编码为 Opus
→ WebSocket 按 60 ms 帧下发
```

当前 `edge.py` 已增加计时埋点，但没有重新启用历史失败的音频流式转码。

历史结论保持有效：

- feature 曾加入“Edge 增量 MP3 → ffmpeg low-delay → PCM → Opus”的自定义流式实现。
- 实际表现为每个服务端分句开头被吞，客户端收到的 Opus 包数量却与服务端 PCM 量基本一致。
- 将客户端预缓冲从约 120 ms 增加到约 600 ms 无明显改善。
- 恢复稳定完整 MP3 路径后，语音完整播放。
- 因此旧实现的根因在自定义 Edge 流式转码路径，不在 ASR、LLM、MCP、Token、WebSocket 或客户端声卡预热。

## 3.6 TTS 延迟诊断与本地 PCM 探针

最新服务端新增：

```text
core/utils/tts_latency_trace.py
tools/analyze_tts_latency_log.py
tools/tts_pcm_pipeline_probe.py
tests/test_tts_latency_trace.py
tests/test_tts_pcm_pipeline_probe.py
tests/test_tts_latency_source_contract.py
```

计时默认关闭，可通过以下环境变量开启：

```text
XIAOZHI_TTS_LATENCY_TRACE=1
```

主要阶段：

```text
turn_started
llm_request_started
llm_first_delta
llm_stream_completed
tts_text_received
tts_segment_ready
tts_provider_request_started
tts_provider_first_audio_chunk
tts_provider_audio_completed
tts_first_opus
tts_sentence_start_sent
tts_first_ws_audio_send
tts_stop_sent
TTS_LATENCY_SUMMARY
```

探针经过修正后，将生产发送链路导入延迟到 `_run()`，并在导入时替换日志初始化，因此不依赖部署目录中的 `data/.config.yaml`。

本地 24 kHz、1.2 秒、440 Hz PCM 探针结果：

| 指标 | 结果 |
|---|---:|
| PCM 字节 | 57600 |
| Opus 包数 | 20 |
| WebSocket 包数 | 20 |
| PCM 生成 | 0.577 ms |
| PCM ready → 第一包 Opus | 1.371 ms |
| Opus 编码总耗时 | 8.384 ms |
| 第一包 Opus → 第一次 WS send | 7.265 ms |
| 第一包到最后一包 WS send | 840.923 ms |
| 总探针耗时 | 850.239 ms |
| 状态 | `ok` |

`first_to_last_ws_send_ms≈840 ms` 与当前发送流控一致：1.2 秒音频共 20 个 60 ms 包，前若干包预发后，其余包按实时节奏下发。它不代表编码耗时。

该探针证明：

```text
已有 PCM
→ Opus 编码
→ 开始 WebSocket 发送
```

不是当前秒级首音频延迟来源。

## 3.7 实测延迟结论

稳定 Edge 基线的典型中位数：

| 阶段 | p50 |
|---|---:|
| LLM 请求 → 首 Token | 1.029 s |
| LLM 首 Token → 完整回复 | 0.341 s |
| 第一分句 → 第一包 WS 音频 | 2.308 s |
| 整轮 → 第一包 WS 音频 | 4.131 s |

换成千问 `qwen3.7-plus` 后，最新 10 轮典型中位数：

| 阶段 | p50 |
|---|---:|
| LLM 请求 → 首 Token | 0.867 s |
| LLM 首 Token → 完整回复 | 0.444 s |
| 第一分句 → 第一包 WS 音频 | 2.109 s |
| 整轮 → 第一包 WS 音频 | 3.478 s |

主结论不变：

```text
LLM 完整输出典型约 1.3 s
Edge 到第一包 WS 音频典型约 2.1 s
```

Edge 内部典型拆分：

```text
请求 Edge → 第一块 MP3       约 1.46 s
第一块 MP3 → 完整 MP3        约 0.52 s
完整 MP3 → 第一包 Opus       约 0.12 s
第一包 Opus → WebSocket      约 0.001 s
```

最严重的一次长尾记录：

```text
请求 Edge → 第一块 MP3       约 6.95 s
请求 Edge → 完整 MP3         约 8.65 s
整轮 → 第一包 WS 音频        约 10.44 s
```

因此需要区分两件事：

1. **Edge 上游首 chunk 延迟**：典型约 1–2.5 秒，偶发可接近 7 秒；本地流式解码无法消除。
2. **等待完整 MP3 的串行损失**：典型约 0.5 秒，长句可超过 2 秒；新的安全流式流水线可以把它与解码、编码和发送重叠。

下一阶段做 Edge 流式解码，不是因为 MP3→PCM→Opus 本身“太慢”，而是因为当前服务端已经拿到首批 MP3，却仍等待全部 MP3 后才开始解码。新的实现必须与旧失败实现完全区分。

## 4. PC 客户端相对旧 master 的核心变化

## 4.1 单进程 Runtime 重写

客户端已经从旧 sidecar / 多进程拼装结构改为单进程主线：

- PySide/QML UI 与 Assistant Runtime 同进程。
- qasync/asyncio 统一事件循环。
- 本地 Notes application/service/repository 边界。
- 单线程数据库 executor，避免跨线程 SQLite/SQLAlchemy 所有权混乱。
- Assistant event/reducer/effect 状态机。
- WebSocket transport、重连 generation、协议路由。
- Opus 下行解码与 PyAudio 输出。
- 连续对话、PTT、播放结束驱动下一轮。
- 本地 MCP coordinator、registry、executor、confirmation 和 UI bus。

旧 `services/*` sidecar、旧 controller/model 页面结构已从 rewrite 主线移除，不应恢复。

## 4.2 客户端 MCP 当前职责

当前客户端主要承担“资源实际所在地”的执行职责：

- 本地便签数据库读写。
- 标题、正文、标签、待办、置顶、删除与恢复。
- 本地 UI 页面跳转和列表刷新。
- 确认卡片展示与高风险操作最终提交。
- 将真实结构化结果返回服务端。

服务端负责：

- 意图与候选路由。
- 多轮 workflow。
- 参数收集与调用顺序。
- Token 预算。
- 真实性校验和面向用户的最终回复。

这是当前冻结边界。后续不会把本地数据库和 PC UI 控制无条件搬到服务端。

## 4.3 泛化搜索失败关闭

客户端 `intent_rules.py` 与 Gate 5.1 executor 已加强：

- 清理自然口语中的礼貌词、动作词和便签包装词。
- 泛化请求不能退回使用原始“便签”“笔记”等无意义词搜索。
- `extract_search_terms()` 为空时返回 `missing_search_terms`。
- `notes.search` 保持 fail-closed，避免随机返回任意记录。
- `notes.resolve(query="3")` 防御性兼容为可见标题精确匹配，但不能把数字直接当数据库 ID。
- 上下文指代没有稳定目标时返回候选或澄清，不偷偷选择。

## 4.4 搜索结果表达

Gate 5.1 已不再统一返回“搜索完成”。

当前行为：

- 0 条：说明没有找到与当前请求匹配的结果。
- 1 条：返回数量与标题。
- 多条：返回数量与前几个标题，结果过多时标记省略。

注意：这是工具层基础 message。最终面向用户的自然表达仍应由服务端结合用户原请求和结构化结果生成，不能在 Prompt 中写死测试实体。

## 4.5 高风险确认事务

客户端确认层支持：

- 修改正文、删除等高风险操作先创建 pending confirmation。
- 本地确认和语音确认进入统一确认服务。
- 同一 confirmation ID 单飞，避免重复点击和重复提交。
- 执行期间按钮进入处理中状态。
- 确认、拒绝、失败、超时均产生终态。
- 最终数据库提交后才返回 `committed=true` 或对应真实结果。
- 弹窗在所有终态关闭，而不是只在成功时关闭。

这解决了“点击确认后数据库可能完成，但语音助手一直思考，只能手工重连”的闭环缺口。

## 4.6 全会话生命周期监督

新增：

```text
app/assistant/lifecycle_supervisor.py
```

它是最后一道保险，不替代正常 reducer/transport/playback 终态。

监督范围：

- 文本回合
- PTT / 普通语音回合
- 连续对话回合
- 工具回复后的 TTS
- 本地确认/拒绝
- 没有 active token 却卡在忙碌 phase 的异常状态

当前主要阈值：

```text
文本回合：40 秒
普通语音回合：60 秒
连续对话忙状态：35 秒
orphan busy：8 秒
本地确认正常宽限：4 秒
本地确认播放宽限：12 秒
```

恢复机制：

- 记录明确 lifecycle recovery 原因。
- 关闭旧 transport。
- 取消旧回合任务。
- 失效旧 capture/playback/connection generation。
- 创建新 connection generation 并自动重连。
- 迟到的旧事件不能污染新会话。

已知风险：

- `orphan_busy` 规则较激进，曾出现页面工具回复期间被覆盖的现象。
- 后续如果再出现误恢复，应优先增加明确的工具/UI 活动所有权，而不是简单继续缩短或拉长固定超时。

## 4.7 播放断流与无限静音恢复

新增播放 packet-idle watchdog：

```text
PLAYBACK_PACKET_IDLE_WATCHDOG_V1
```

触发条件同时要求：

- 已收到过 Opus。
- 已解码出 PCM。
- 输入流还没有收到 terminal / `tts_stop`。
- PCM 缓冲已经耗尽。
- 连续约 8 秒没有新二进制音频包。

触发结果：

```text
playback_packet_idle_timeout
→ 停止 PyAudio
→ 取消无限补零静音
→ RuntimePlaybackFailed
→ lifecycle supervisor 延迟约 0.5 秒
→ 主动重连并更换 generation
```

真实故障注入：

- 回复播放中执行 `docker pause xiaozhi-esp32-server`。
- 客户端能在约 8–9 秒后停止假播放并自动恢复。
- `docker unpause` 后可自动重新连接。
- 不再要求用户手工点“重连”。

当前客户端正常启动预缓冲保持：

```text
startup_prebuffer_chunks = 2
```

此前改为 10 仅用于排查吞音，已证明不是修复方向，不应再次提交。

---

## 4.8 TTS 6 秒 fallback 生命周期修复

最新客户端提交：

```text
429520d6f66564cbe6c84ad9b42a313620ad6d7a
fallback fix
```

已确认的旧故障链：

```text
收到 tts start
→ 启动 6 秒 tts_state_fallback
→ 6 秒后提前完成回合并清空 active turn token
→ Edge 晚到的 sentence_start 无法创建播放流
→ 后续 Opus 虽被 WebSocket 收到，但没有有效播放上下文
→ 客户端保持 idle，用户听到吞音
```

最新修复：

- 删除 `TEXT_TTS_FALLBACK_SECONDS = 6.0`。
- 引入 `TTS_LIFECYCLE_TIMEOUT_SECONDS = 60.0`，只作为异常卡死恢复。
- 文本和语音回合分别记录 `text_tts_lifecycle_started` / `voice_tts_lifecycle_started`。
- 收到任意非终止 TTS state 后，TTS 生命周期取得该回合所有权。
- 生命周期期间，普通 LLM/assistant 文本不会再触发 1.5 秒 settle 提前完成。
- 只有明确 terminal TTS state、abort、断线或长生命周期 watchdog 才完成回合。
- 晚于旧 6 秒阈值到达的 `sentence_start` 仍能取得 turn token 并创建播放流。
- 文本-only、没有 TTS 生命周期的回复仍保持原短 settle 行为。

新增定向测试：

```text
test_text_tts_start_survives_old_six_second_fallback
test_voice_tts_start_keeps_turn_until_terminal_state
test_text_only_response_still_uses_short_settle
```

注意：`PlaybackCoordinator` 中“已经收到 `sentence_start`，但之后 6 秒仍没有任何二进制包”的 stream-start watchdog 与本次 bug 不同。它从真实媒体边界开始计时，用于检测已开始的坏流，不应与旧的“从 TTS start 提前结束整个回合”混为一谈。

当前提交层修复已完成，但本 Checkpoint 尚未记录该 HEAD 的 Windows 定向测试输出和真实 delayed-TTS 验收结果。因此状态应标记为：

```text
代码已修复，真实链路待验收
```

## 5. 端到端工具链当前已验证行为

阶段性真实验收通过的主要行为：

### 查询

- “查一条便签”会追问关键词，不随机返回。
- 一次说完整关键词可以直接搜索。
- “最近几条”使用最近列表。
- 搜索无结果不会只说“搜索完成”。
- 当前仍是关键词搜索，不是语义向量搜索。

### 新增

- 可多轮收集标题和正文。
- 参数补充轮保持 pending workflow。
- 创建后真实刷新客户端列表。

### 修改

- 可先定位目标，再追问修改内容。
- 数字标题使用 `exact_title`。
- 修改正文进入确认事务。
- 确认前不宣称完成，确认后才提交数据库。

### 删除

- 可按标题或正文描述定位。
- 删除进入确认事务。
- 确认后软删除并进入已删除列表。

### 标签和页面

- 标签绑定、解绑和标签页导航可用。
- 可打开全部、标签、回收站、置顶、待办等页面。
- 页面动作必须有真实 UI dispatch 结果，不能只由模型口头声称“已打开”。

### 对话模型与延迟实验

- DashScope 上的 `deepseek-v4-flash` 与千问 `qwen3.7-plus` 均能正常完成普通聊天与工具链。
- 千问 `qwen3.7-plus` 的首 Token 通常更快，但完整回复后的净改善有限；Edge 仍是更大的首音频延迟块。
- 直连 DeepSeek 曾出现输出 Token 全部被 reasoning 消耗、可见正文为空的情况。当前决定是不继续使用直连配置，本阶段暂不改该兼容问题。
- LLM 切换实验没有改变 Edge 为主要延迟来源的结论。

### 音频可靠性

- 正常 Edge 延迟低于旧 6 秒阈值时，客户端可以正常建立播放流。
- Edge 长尾跨过旧 6 秒阈值时，已复现“服务端发送、客户端收包、播放器不启动”。
- 根因已定位到客户端短 fallback 提前清空 turn token，而不是服务端未发送音频。
- 最新客户端代码已修复该生命周期规则，等待真实 delayed-TTS 验收。

## 6. 已知测试与验收证据

## 6.1 服务端

当前诊断镜像内已记录：

- TTS latency trace / PCM probe / source contract：**5 项通过**。
- 工具路由、workflow、参数归一化与 source contract：**56 项通过**。
- LLM usage、OpenAI streaming 与 client config：**25 项通过**。
- Sherpa SenseVoice B2：**3 项通过**。
- PCM 探针：`status=ok`，20 个 Opus 包与 20 个 WebSocket 包完全对齐。
- 真实 Edge 计时已经覆盖稳定基线、千问实验和一次约 7 秒 Edge 首 chunk 长尾。

测试必须在服务端镜像的 Python 3.10 运行环境中执行。宿主机 Python 3.14 缺少项目固定依赖，不能把宿主机 import blocker 当作正式回归结果。

当前最低相关回归：

```bash
cd main/xiaozhi-server

PYTHONPATH=. python -m unittest -v   tests.test_tts_latency_trace   tests.test_tts_pcm_pipeline_probe   tests.test_tts_latency_source_contract

PYTHONPATH=. python -m unittest -v   tests.test_tool_routing   tests.test_tool_workflow   tests.test_tool_routing_generic_note_search   tests.test_tool_workflow_v2   tests.test_tool_argument_normalization   tests.test_tool_routing_v2   tests.test_connection_workflow_source_contract

PYTHONPATH=. python -m unittest -v   tests.test_llm_usage   tests.test_openai_usage_streaming   tests.test_openai_client_config

PYTHONPATH=. python -m unittest -v tests.test_sherpa_sensevoice_b2
PYTHONPATH=. python tools/tts_pcm_pipeline_probe.py
```

已知仓库卫生问题：

```text
tools/tts_pcm_pipeline_probe.py.before-probe-fix1-20260804162726
```

当前远程分支误提交了这个备份文件，下一次服务端提交应删除。

## 6.2 客户端

历史阶段曾确认：

- `tests/lifecycle_fix`：11 项通过。
- `tests/gate5_1 + tests/gate5_3`：35 项通过。
- `tests/gate2_5 + tests/gate3_4 + tests/gate4_4`：71 项通过。
- 泛化搜索相关测试：3 + 3 项通过。
- 播放断流真实故障注入通过。

最新 `fallback fix` 提交新增 3 项定向测试，但本 Checkpoint 尚未附上该提交后的实际 pytest 输出。进入 Edge 优化前必须补齐：

```powershell
$env:PYTHONPATH = (Resolve-Path ".pps
otes-pyside").Path

python -m pytest -W error -q tests\test_tts_lifecycle_fallback.py
python -m pytest -W error -q -k "websocket or playback or tts"

python -m pytest -W error -q tests\lifecycle_fix
python -m pytest -W error -q tests\playback_stall_fix
python -m pytest -W error -q tests\gate4_1 tests\gate4_2 tests\gate4_3 tests\gate4_4
python -m pytest -W error -q tests\gate5_1 tests\gate5_3
python -m pytest -W error -q tests\gate2_5 tests\gate3_4
```

真实 delayed-TTS 验收至少覆盖：

1. `sentence_start` 在 6.5 秒后到达，仍启动播放。
2. `sentence_start` 在 10 秒后到达，仍启动播放。
3. 旧 6 秒时点不产生 `TextTurnCompleted(reason=tts_state_fallback)`。
4. terminal `tts stop` 到达后仅完成一次。
5. 用户 abort / 新一轮输入能明确取消旧播放。
6. 断线和 generation 切换后，旧音频不能污染新会话。
7. 服务端真正不返回 terminal 时，60 秒生命周期 watchdog 能有界恢复。

上述历史数字和新增测试文件都不代表未来 HEAD 自动全绿；每次正式改动仍要重新运行。

## 7. 当前部署结构

自部署栈：

```text
xiaozhi-esp32-server       语音、LLM、工具与 MCP 编排
xiaozhi-esp32-server-web   智控台，通常暴露 8002
xiaozhi-esp32-server-db    MySQL
xiaozhi-esp32-server-redis Redis
```

服务端常用端口：

```text
8000 WebSocket
8003 HTTP / OTA / 视觉接口
8002 智控台 Web
```

模型和数据通过宿主机挂载，重建 Server 容器不应重建或删除 MySQL、Redis、Web 和数据目录。

推荐部署原则：

- 每次构建使用新镜像标签，不覆盖上一稳定镜像。
- 使用独立 Compose override 只替换 `xiaozhi-esp32-server`。
- 使用 `--no-deps --force-recreate --no-build --pull never` 切换 Server。
- 至少保留一个已验证镜像和 Compose 文件用于回滚。
- A/B 镜像只用于验证，验证后从正式 feature commit 重建发布镜像。

---

## 8. 当前明确不做的事情

### 8.1 暂不大重构客户端 MCP

原因：

- 当前增删改查、确认、标签、页面和生命周期已经可用。
- 后续 MCP 重心计划迁移到服务端编排。
- 现在重构客户端会重复投入，并可能破坏刚稳定的行为。

客户端先保持：

```text
Local Note Executor
Confirmation Adapter
UI Adapter
Local Repository / Application Service
```

未来服务端编排稳定后，再删除 Gate 历史层和收薄客户端结构。

### 8.2 暂不做智能语义搜索

当前搜索是确定性关键词匹配。

“王总报价”不能匹配“王总屏幕报价”中的非连续语义，不属于当前阶段 bug。后续如果需要，可单独设计：

- 分词召回
- 多关键词 AND/OR
- 模糊匹配
- 向量检索
- 混合排序

不能为了“智能”恢复泛化查询随机命中。

### 8.3 不恢复旧 Edge 自制流式转码

不得直接恢复历史失败实现：

```text
多个短生命周期 ffmpeg
+ nobuffer / low_delay
+ 边收边随意切片
+ 缺少尾部 drain / fallback
```

后续允许新的 Edge 流式实验，但必须满足：

- 每个分句只使用一个持续存在的解码进程。
- MP3 写入与 PCM 读取并发执行。
- Edge 结束后关闭 stdin，并完整 drain stdout 尾部。
- Opus 最终帧使用明确 end-of-stream。
- 中断时同时取消 Edge、ffmpeg、编码和发送队列。
- 流式失败自动回退当前稳定完整 MP3 路径。
- 默认由 feature flag 关闭。
- 必须有音频内容级完整性测试和真实 A/B。

### 8.4 暂不修直连 DeepSeek 空正文兼容

直连 DeepSeek 曾出现 reasoning 消耗全部输出预算、可见正文为空。当前决定：

- 不使用直连 DeepSeek 配置。
- 继续使用已验证的 DashScope / 千问配置。
- 本阶段不改 OpenAI 兼容参数和空正文兜底。
- 若未来重新启用直连 DeepSeek，再单独建立兼容任务卡。

### 8.5 客户端修复期间不同时改 Edge

先完成客户端 delayed-TTS 生命周期真实验收，再开始 Edge 改动。否则一旦仍有吞音，将无法区分是客户端生命周期问题还是服务端流式音频问题。

## 9. 下一阶段：先客户端可靠性，再 Edge TTS 延迟

## 9.1 客户端 delayed-TTS 生命周期真实验收

当前第一优先级不是继续缩短延迟，而是证明晚到音频不会再被客户端吞掉。

验收路径：

```text
用户提交回合
→ 收到 tts start
→ 等待超过旧 6 秒阈值
→ sentence_start 到达
→ 创建 PlaybackCoordinator stream
→ Opus 到达
→ ActualPlaybackStarted
→ tts stop
→ 回合终态
```

验收要求：

- 6.5 秒、10 秒和一次真实 Edge 长尾均能正常播放。
- 旧 6 秒时点不清除 active turn token。
- 晚到二进制音频不再停留在 `audio_before=idle / audio_after=idle`。
- terminal 只完成一次，不产生重复终态。
- 用户插话、abort、新回合和断线仍可取消旧生命周期。
- 60 秒 watchdog 仅用于真正无终态的异常流，不参与正常慢 TTS。
- 播放 packet-idle watchdog 继续保留，用于已经开始播放后断流。

完成这一步之前，不进入服务端 Edge 代码改造。

## 9.2 Edge TTS 优化路线

客户端可靠性验收通过后，Edge 优化分两条并行评估。

### 路线 A：安全的 Edge MP3 流式解码

目标不是优化约 120 ms 的本地解码本身，而是把以下阶段重叠：

```text
继续接收剩余 MP3
PCM 解码 / 重采样
Opus 编码
WebSocket 发送
```

推荐流水线：

```text
Edge MP3 chunks
→ 单一持久 ffmpeg stdin
→ 并发持续读取 stdout PCM
→ 固定帧 OpusEncoder
→ 第一包 Opus 立即发送
→ Edge 完成后 drain 尾部
```

预期收益主要来自“首 chunk 后等待完整 MP3”的部分：

- 典型约 0.4–0.7 秒。
- 长句可能超过 1–2 秒。
- 无法消除 Edge 请求到首 chunk 的上游延迟。

必须保留稳定 buffered fallback，并用同一批固定问题做 A/B。

### 路线 B：评估原生流式 TTS Provider

Edge 首 chunk 本身典型约 1–2.5 秒，并有接近 7 秒的长尾。即使安全流式解码成功，仍可能无法达到理想交互延迟。

新 Provider 首选：

- 原生流式 PCM，或边界明确的流式音频帧。
- 支持连接复用。
- 支持当前会话取消。
- 首块音频稳定且包含句首。
- 输出可稳定转换到 24 kHz 单声道 Opus。
- terminal 可靠。
- 连续 20 轮无吞音、串音、假播放或残留状态。

建议阶段指标：

- 首音频目标 500–800 ms，阶段上限约 1.2 s。
- 分句间空窗尽量低于 300 ms。
- 打断后 500 ms 内停止旧音频。
- 服务端包数与客户端接收、解码、播放指标对齐。

一次只换 TTS Provider，不同时改 MCP、ASR、Token 或客户端协议。

## 9.3 再评估 ASR

B2 当前准确性和本地推理速度可用，但完整话语后才返回 final。

下一阶段 ASR 重点不是只换模型，而是测量：

- 开口到首个 partial。
- 停止说话到 final。
- VAD 尾静音等待。
- 数字、标题、标签、时间等实体准确率。
- 连续对话、打断和短命令稳定性。

只有真实数据证明 VAD/整句模式成为主要延迟后，再引入在线/两遍式 ASR。

## 10. 后续 MCP 目标架构

长期推荐：

```text
用户语音
  ↓
服务端：路由、workflow、参数收集、预算、调用计划、真实性校验
  ↓
客户端：执行本地数据库、UI、文件与系统资源能力
  ↓
客户端：结构化真实结果 / 本地确认结果
  ↓
服务端：根据真实结果生成自然回复
```

不建议把所有 MCP 都搬到服务端：

- PC 本地数据库、UI、麦克风和操作系统权限仍属于客户端。
- 全搬服务端会扩大隐私边界、增加同步冲突并失去离线能力。

可以迁移到服务端的是：

- 意图路由
- workflow 状态机
- 参数归一化
- 工具调用顺序
- Token/成本限制
- 超时、审计与真实性判断

---

## 11. 不可回归的核心约束

后续任何修改都必须保留：

1. 普通聊天不得默认携带全量工具。
2. 泛化查询不得随机返回任意本地记录。
3. 用户可见数字不得直接解释为数据库 ID。
4. 参数补充轮不得因为没有领域词而丢失 pending workflow。
5. 工具失败、歧义和缺参不得销毁当前任务。
6. 没有真实 mutation outcome 时不得声称创建、修改、删除或发送成功。
7. 高风险操作必须经过确认服务真实提交。
8. 所有会话周期最终必须进入终态；异常时自动失效旧 generation 并恢复。
9. 下行 TTS 断流不得无限输出静音。
10. 收到非终止 TTS state 后，不得使用短固定超时提前清除 active turn token。
11. 晚到的 `sentence_start` 和首个 Opus 必须仍可建立并启动对应回合的播放流。
12. 正常 TTS 生命周期应由 terminal、abort、断线或长异常 watchdog 结束。
13. 已收到 `sentence_start` 后长期无二进制包的 media watchdog 可以保留，但不得误伤尚未开始的慢 TTS。
14. TTS Provider 优化必须以音频完整性优先，不能只追求首包延迟。
15. 新 Edge 流式实现必须有稳定 buffered fallback。
16. TTS trace 必须保持 opt-in，不记录提示词、正文、音频 payload、密钥或工具参数。
17. ASR、TTS、MCP、Token 和客户端播放每次只改一个主要变量，保持 A/B 可归因。
18. 模型权重、密钥和用户数据不得进入 Git。
19. 本地修复备份、临时日志和探针输出不得提交到正式分支。

## 12. 推荐的下一张任务卡

### 标题

`Client Delayed-TTS Lifecycle Real Acceptance`

### 目标

在不修改服务端 Edge、LLM、ASR、MCP 和 Token 逻辑的前提下，验证客户端 `fallback fix` 能在首音频超过旧 6 秒阈值时保留回合和播放归属，彻底消除“收包但不播放”的吞音。

### 交付物

- 最新客户端 HEAD、测试命令和测试输出。
- 6.5 秒与 10 秒 delayed `sentence_start` 自动化测试。
- 一次真实 Edge 长尾或可控延迟注入。
- 客户端 `TtsPlaybackStreamStarted`、`ActualPlaybackStarted`、terminal 完成日志。
- 服务端 `tts_first_ws_audio_send` 与客户端首包/首播时间对齐。
- abort、新回合、断线和 generation 切换回归。
- 60 秒生命周期 watchdog 故障恢复证明。
- 验收通过后冻结客户端播放生命周期，再创建下一张 `Edge Buffered-to-Streaming Decode A/B` 任务卡。

## 13. 主要源码与报告索引

### 服务端

```text
docs/checkpoint/SELF_HOSTED_VOICE_ASSISTANT_STAGE_CHECKPOINT_2026-08-04.md
docs/report/ASR_B2_SHERPA_SENSEVOICE_DELIVERY_REPORT.md
docs/report/LLM_TOKEN_BUDGET_LAYERED_ROUTING.md
docs/report/LLM_TOKEN_USAGE_PHASE1.md
docs/report/LLM_TOKEN_USAGE_PHASE2.md
docs/report/LLM_TOKEN_USAGE_PHASE3.md
main/xiaozhi-server/core/providers/asr/sherpa_sensevoice_b2.py
main/xiaozhi-server/core/providers/llm/usage.py
main/xiaozhi-server/core/providers/tools/tool_routing.py
main/xiaozhi-server/core/providers/tools/tool_workflow.py
main/xiaozhi-server/core/providers/tools/tool_arguments.py
main/xiaozhi-server/core/providers/tools/device_mcp/mcp_executor.py
main/xiaozhi-server/core/connection.py
main/xiaozhi-server/core/providers/tts/base.py
main/xiaozhi-server/core/providers/tts/edge.py
main/xiaozhi-server/core/handle/sendAudioHandle.py
main/xiaozhi-server/core/utils/tts_latency_trace.py
main/xiaozhi-server/tools/analyze_tts_latency_log.py
main/xiaozhi-server/tools/tts_pcm_pipeline_probe.py
main/xiaozhi-server/tests/test_tts_latency_trace.py
main/xiaozhi-server/tests/test_tts_pcm_pipeline_probe.py
main/xiaozhi-server/tests/test_tts_latency_source_contract.py
```

### PC 客户端

```text
pc-app-build/docs/PC_ASSISTANT_RUNTIME_MASTER_PLAN.md
pc-app-build/docs/adr/ADR-001-single-process-qasync.md
pc-app-build/docs/adr/ADR-008-gate4-playback-and-auto-next-turn.md
pc-app-build/docs/adr/ADR-009-in-process-xiaozhi-mcp-tool-runtime.md
pc-app-build/apps/notes-pyside/app/assistant/mcp/intent_rules.py
pc-app-build/apps/notes-pyside/app/assistant/mcp/gate5_1_executor.py
pc-app-build/apps/notes-pyside/app/assistant/mcp/gate5_2_executor.py
pc-app-build/apps/notes-pyside/app/assistant/mcp/gate5_3_executor.py
pc-app-build/apps/notes-pyside/app/assistant/mcp/confirmation.py
pc-app-build/apps/notes-pyside/app/assistant/lifecycle_supervisor.py
pc-app-build/apps/notes-pyside/app/assistant/network/websocket_transport.py
pc-app-build/apps/notes-pyside/app/assistant/network/playback_websocket_transport.py
pc-app-build/apps/notes-pyside/app/assistant/playback/coordinator.py
pc-app-build/apps/notes-pyside/app/ui/mcp_ui_adapter.py
pc-app-build/tests/test_tts_lifecycle_fallback.py
```

## 14. Checkpoint 状态

- **服务端远程 HEAD**：`997ab96604d0812fc7775a6ae247f40f10dcc3dd`。
- **客户端远程 HEAD**：`429520d6f66564cbe6c84ad9b42a313620ad6d7a`。
- **ASR B2**：阶段稳定，可作为后续流式 ASR 的准确性与本地部署基线。
- **Token/usage**：已投入运行，需继续保持路由与 workflow 正确性。
- **服务端工具增强**：主要功能真实可用，后续以服务端编排为主线继续演进。
- **客户端 MCP**：功能冻结，暂不做大重构，只修明确缺陷。
- **播放 packet-idle 恢复**：已完成历史真实故障注入。
- **客户端 6 秒 fallback**：代码已修复，真实 delayed-TTS 链路待验收。
- **TTS 正确性**：稳定 buffered Edge 路径可用。
- **TTS 可观测性**：全链路 trace、分析器和本地 PCM 探针已加入并完成服务端镜像验证。
- **TTS 延迟结论**：Edge 是当前首音频主要延迟块；本地 PCM→Opus→WS 不是主要瓶颈。
- **Edge 风险**：首 chunk 存在明显长尾；等待完整 MP3 还引入额外串行损失。
- **LLM 供应商实验**：换成千问 `qwen3.7-plus` 后整体略快，但没有改变 Edge 占大头的结论。
- **直连 DeepSeek**：当前停用，不在本阶段修复空正文兼容。
- **仓库卫生**：服务端误提交的 probe 备份文件待删除。
- **下一步**：先完成客户端 delayed-TTS 生命周期真实验收；通过后再进入 Edge 安全流式解码或原生流式 TTS Provider A/B。
