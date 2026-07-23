# ASR B2 Sherpa SenseVoice 交付报告

## 结论

B2 已在独立分支实现为可选 ASR provider。默认 FunASR 未改变，PC 客户端
VAD 未改变，也没有引入额外的常驻 WebSocket/HTTP ASR 服务。

B2 复用了同事方案中已被 B1 证明有效的识别侧组合：

- Sherpa-ONNX `OfflineRecognizer`；
- SenseVoice FP32 ONNX；
- 固定中文、ITN、4 个推理线程；
- 每个完整话语使用全新的 Silero VAD；
- 0.5 秒尾静音；
- 320/240 ms 前后边界补偿；
- 相邻片段的补偿不超过静音间隔的一半；
- 小于 0.3 秒的片段过滤；
- VAD 未命中时回退完整话语，避免远场漏检直接丢句。

## 为什么不是直接运行同事的 Windows 服务

同事的 `/audio-stream` 入口只允许一个音频客户端，并保存跨帧、跨会话状态。
直接把小智的多客户端完整话语再次送入该入口，会引入双重端点控制、结果关联和
单连接瓶颈。

B2 把与本次问题直接相关的“句内边界精修”内嵌到 Xiaozhi provider，同时让
客户端/协议继续拥有轮次结束权。每次请求使用独立 VAD 状态，不会串到下一轮。

## 代码交付

- `core/providers/asr/sherpa_sensevoice_b2.py`：新 provider。
- `tests/test_sherpa_sensevoice_b2.py`：边界补偿单元测试。
- `config.yaml`：源码配置模式示例，默认选择仍为 FunASR。
- `202607231900.sql`：管理后台 provider/model 配置迁移。
- `db.changelog-master.yaml`：加载 B2 迁移。
- `docs/sherpa-sensevoice-b2.md`：模型挂载、启用和回滚说明。

模型权重未进入 Git。

## 验证环境

- 服务器运行时：现有 `xiaozhi-server:asr-a2` 依赖层；
- B2 源码镜像：`xiaozhi-server:asr-b2`；
- Sherpa-ONNX：服务器镜像现有 1.12.29；
- 模型：同事工程中的 SenseVoice FP32 `model.onnx`、`tokens.txt`、
  `silero_vad.onnx`；
- 所有离线推理容器使用 `--network none`，未启动客户端、LLM、MCP 或 TTS。

## 结果

### 20 条中文回归

20 条均产生非空中文/中英混合转写，整体与 B1 的同事完整管线约 18/20 的
语义水平一致。数字、日期、电话号码、局域网地址和普通中文句未出现系统性退化。
单句推理约 0.16–0.30 秒（不含首次模型加载）。

### 控制距离

| 距离 | B2 结果 |
|---|---|
| 20 cm | `明天下午3点在2号会议室讨论项目进度。` |
| 50 cm | `明天下午3点，在2号会议室讨论项目记录。` |
| 100 cm | `明天下午3点在个号会议室讨论项目进度。` |

### 客户端真实 ingress

| 距离 | B2 结果 |
|---|---|
| 20 cm | `明天下午3点在2号会议室讨论项目进度。` |
| 50 cm | `明天下午3点再拉会议室，讨论下目进度。` |
| 100 cm | `明天下午3点才公司好这相个经来。` |

真实 100 cm 的默认 FunASR 基线为 `In offer on.`。B2 没有恢复完整句意，但
稳定保留了中文、时间开头，并消除了英文误判。最终 B2 镜像的重复验证得到相同
结果。

## 自动检查

- B2 边界算法单元测试：3/3 通过；
- Python 编译检查：通过；
- `git diff --check`：通过；
- B2 源码镜像构建：通过；
- 最终镜像真实 100 cm 推理：通过；
- 一次性 MySQL 中执行基础表结构和 B2 Liquibase SQL：通过；
- provider `fields` 与 model `config_json` 的 `JSON_VALID`：均为 1；
- 一次性数据库无持久卷，验证后已删除。

## 已知边界

B2 改善了远场语言稳定性，但不能把低信噪比录音恢复成近场质量。真实 50/100 cm
仍有明显替换错误。B2 的定位是本地、可回滚的工程基线，不是最终宣布模型胜出。

方案 C 应继续使用同一批 26 条音频和同一报告字段，具体两遍流式实现暂不在 B2
中预设。
