# 自部署语音助手阶段 Checkpoint

- **日期**：2026-08-04
- **范围**：服务端 `ER1C-6832/xiaozhi-multiclient-server` 与 PC 客户端 `ER1C-6832/note-assistant-app`
- **服务端开发分支**：`feature/asr-sherpa-sensevoice-b2`
- **客户端开发分支**：`rewrite/single-process-runtime`
- **用途**：记录当前相对上游/旧主线的功能差异、已验证能力、部署边界、已知风险和下一阶段入口，作为后续 ASR/TTS、服务端 MCP 编排和客户端收薄工作的恢复点。

---

## 1. 当前阶段结论

当前系统已经从“官方服务 + 旧多进程 PC 客户端”演进为一套可实际运行的自部署链路：

1. 服务端加入本地 Sherpa-ONNX + SenseVoice B2 ASR 工程基线。
2. 服务端加入 Token usage 统计、chat/tool 分层预算、工具候选裁剪与结构闸门。
3. 服务端加入确定性工具路由、跨轮 durable workflow、参数归一化和工具执行真实性保护。
4. PC 客户端已重写为单进程 Runtime，并在本地执行便签、标签、UI 与确认类 MCP 能力。
5. 客户端补齐了泛化查询失败关闭、数字标题解析、确认事务闭环、会话生命周期监督和播放断流自动恢复。
6. 服务端曾尝试自定义 Edge TTS 流式 MP3 转码，但真实 A/B 证明会造成分句开头吞音，现已恢复上游稳定 `edge.py`。
7. 当前基础能力可用，但 Edge TTS 仍是整段生成，首音频延迟较高；下一阶段优先替换为可靠的原生流式 TTS，随后再评估流式 ASR。

当前不建议立即大规模重构客户端 MCP。客户端先冻结为“本地能力执行端”，后续将意图、workflow、工具规划和真实性判断进一步收敛到服务端。

---

## 2. Git 分支快照

### 2.1 服务端

GitHub 对比 `main...feature/asr-sherpa-sensevoice-b2` 的阶段快照：

- 状态：`diverged`
- feature 相对 main：**ahead 25 commits**
- feature 相对 main：**behind 5 commits**
- 对比时 main 基准提交：`50895d6e332436b37d0fd8e08268b9a2617c5cbe`
- merge base：`de45f73efdd24e9343427a56b5d22f857b6bb7a7`

这意味着 feature 不是简单附着在最新 main 上。后续同步上游时，必须重点检查：

- `core/connection.py`
- `core/providers/tools/`
- `core/providers/llm/`
- `core/providers/tts/`
- `config.yaml`
- `manager-api` 的 Liquibase 迁移

不要直接用上游版本覆盖这些文件。

### 2.2 PC 客户端

GitHub 对比 `master...rewrite/single-process-runtime` 的阶段快照：

- 状态：`ahead`
- rewrite 分支相对 master：**ahead 93 commits**
- behind：`0`
- merge base / master 基准：`1917512f3b06112a6f1453e4110209c5f6de81b9`

客户端 rewrite 已经不是局部补丁，而是完整单进程 Runtime 主线。后续不要尝试把旧 `master` 的 sidecar、多进程服务或旧 controller 结构整体合回来。

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

---

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

## 3.5 TTS 当前状态

当前 `feature` 的：

```text
core/providers/tts/edge.py
```

已恢复为上游稳定实现。

历史结论：

- feature 曾加入“Edge 增量 MP3 → ffmpeg low-delay → PCM → Opus”的自定义流式实现。
- 实际表现为每个服务端分句开头被吞，客户端收到的 Opus 包数量却与服务端 PCM 量基本一致。
- 将客户端预缓冲从约 120 ms 增加到约 600 ms 无明显改善。
- 只替换为上游 `main` 的 `edge.py` 后，语音完整播放。
- 因此根因确认在自定义 Edge 流式转码路径，不在 ASR、LLM、MCP、Token、WebSocket 或客户端声卡预热。

当前边界：

- 正确性恢复，但 Edge 仍是非流式整段生成。
- TTS 首音频延迟仍是交互体验主要瓶颈。
- 下一阶段应替换 Provider，而不是再次修改客户端播放协议。

---

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

### 对话模型

- DeepSeek 和千问均验证过普通聊天与工具链。
- DeepSeek 曾返回 API 503 “Service is too busy”，属于供应商可用性问题，不是本地连接或音频链路问题。

---

## 6. 已知测试与验收证据

## 6.1 服务端

已观察到：

- 新增 workflow / routing / argument / source-contract 测试：23 项通过。
- 旧 routing/workflow/generic-search 回归组共 33 项，曾有 1 个 reason 字段回归；代码修正为保留 `implicit_note_keyword_search` 后再进入部署。
- 工具、确认、增删改查、标签和页面经过真实 PC 客户端验收。
- Edge TTS 单文件 A/B 完成根因确认。

进入下一阶段前建议重新运行：

```bash
cd main/xiaozhi-server
PYTHONPATH=. python -m unittest -v \
  tests.test_tool_workflow_v2 \
  tests.test_tool_argument_normalization \
  tests.test_tool_routing_v2 \
  tests.test_connection_workflow_source_contract

PYTHONPATH=. python -m unittest -v \
  tests.test_tool_routing \
  tests.test_tool_workflow \
  tests.test_tool_routing_generic_note_search
```

## 6.2 客户端

阶段中曾确认：

- `tests/lifecycle_fix`：11 项通过。
- `tests/gate5_1 + tests/gate5_3`：35 项通过。
- `tests/gate2_5 + tests/gate3_4 + tests/gate4_4`：71 项通过。
- 泛化搜索相关测试：3 + 3 项通过。
- 播放断流真实故障注入通过。

后续修改音频或 Runtime 前，最低回归集合：

```powershell
$env:PYTHONPATH = (Resolve-Path ".\apps\notes-pyside").Path

python -m pytest -W error -q tests\lifecycle_fix
python -m pytest -W error -q tests\playback_stall_fix
python -m pytest -W error -q tests\gate4_1 tests\gate4_2 tests\gate4_3 tests\gate4_4
python -m pytest -W error -q tests\gate5_1 tests\gate5_3
python -m pytest -W error -q tests\gate2_5 tests\gate3_4
```

注意：上述数字是阶段中已经出现过的通过记录，不代表未来 HEAD 自动保持全绿。每次正式改动仍要重新运行。

---

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

### 8.3 不再恢复失败的 Edge 自制流式转码

除非有独立实验分支、音频内容级回归和完整 A/B 证据，否则不得重新加入：

```text
Edge 增量 MP3 + ffmpeg nobuffer/low_delay + 即时 Opus
```

---

## 9. 下一阶段：TTS / ASR 延迟工程

## 9.1 优先 TTS

当前仓库具备 Provider 直接替换能力：

```text
selected_module.TTS
→ TTS.<name>.type
→ 动态加载 core/providers/tts/<type>.py
→ TTSProvider
```

客户端只依赖统一下行契约：

```text
sentence_start
→ 24kHz mono Opus binary，60 ms/frame
→ stop
```

新 TTS 首选：

- 原生流式 PCM，或边界明确的流式音频帧。
- 支持连接复用。
- 支持当前会话取消。
- 首块音频稳定且包含句首。
- 输出采样率可稳定转换到 24kHz 单声道。
- 最终终态可靠。

仓库已包含 `DUAL_STREAM / SINGLE_STREAM / NON_STREAM` 抽象和若干流式 Provider，可先做配置级 A/B，再决定是否开发新 Provider。

建议验收指标：

- 首音频目标 500–800 ms，阶段上限约 1.2 s。
- 分句间空窗尽量低于 300 ms。
- 打断后 500 ms 内停止旧音频。
- 连续 20 轮无吞音、无串音、无假播放、无残留状态。
- 服务端 PCM/Opus 包数与客户端接收/解码/播放指标可对齐。

一次只换 TTS Provider，不同时改 MCP、ASR、Token 或客户端协议。

## 9.2 再评估 ASR

B2 当前准确性和本地推理速度可用，但完整话语后才返回 final。

下一阶段 ASR 重点不是只换模型，而是测量：

- 开口到首个 partial。
- 停止说话到 final。
- VAD 尾静音等待。
- 数字、标题、标签、时间等实体准确率。
- 连续对话、打断和短命令稳定性。

只有真实数据证明 VAD/整句模式成为主要延迟后，再引入在线/两遍式 ASR。

---

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
10. TTS Provider 优化必须以音频完整性优先，不能只追求首包延迟。
11. ASR、TTS、MCP、Token 和客户端播放每次只改一个主要变量，保持 A/B 可归因。
12. 模型权重、密钥和用户数据不得进入 Git。

---

## 12. 推荐的下一张任务卡

### 标题

`TTS Provider Streaming A/B and Latency Baseline`

### 目标

在不修改客户端协议、MCP、Token 与 ASR 的前提下，选择一个原生流式 TTS Provider，建立与稳定 Edge 基线一致的 A/B 测试。

### 交付物

- Provider 配置和依赖说明。
- 新旧镜像独立标签。
- 首音频、句间空窗、总合成时间和取消耗时日志。
- 20 轮完整性验收。
- 服务端包数与客户端播放指标对齐报告。
- 一键回滚 Compose override。
- 成功后再决定是否设为默认；失败则完整回退，不污染当前稳定分支。

---

## 13. 主要源码与报告索引

### 服务端

```text
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
main/xiaozhi-server/core/providers/tts/edge.py
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
pc-app-build/apps/notes-pyside/app/assistant/playback/coordinator.py
pc-app-build/apps/notes-pyside/app/ui/mcp_ui_adapter.py
```

---

## 14. Checkpoint 状态

- **ASR B2**：阶段稳定，可作为后续流式 ASR 的准确性与本地部署基线。
- **Token/usage**：已投入运行，需继续保持路由与 workflow 正确性。
- **服务端工具增强**：主要功能真实可用，后续以服务端编排为主线继续演进。
- **客户端 MCP**：功能冻结，暂不做大重构，只修明确缺陷。
- **生命周期与断流恢复**：已补齐并完成真实故障注入。
- **TTS 正确性**：稳定 Edge 路径可用。
- **TTS 延迟**：当前首要未解决问题。
- **下一步**：进入流式 TTS Provider A/B，不同时改动其他主链路。

