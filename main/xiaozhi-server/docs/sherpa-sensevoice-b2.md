# Sherpa SenseVoice B2

B2 是可选的本地 ASR provider，不会替换默认 FunASR。它用于验证同一套
SenseVoice FP32 ONNX 模型在 Sherpa-ONNX 运行时和句内边界精修下的效果。

## 模型目录

默认目录为 `models/sherpa-sensevoice-b2`，需要三个文件：

- `model.onnx`
- `tokens.txt`
- `silero_vad.onnx`

模型文件不提交到 Git。Docker 部署应把宿主机模型目录只读挂载到：

```text
/opt/xiaozhi-esp32-server/models/sherpa-sensevoice-b2
```

## 处理边界

客户端/小智协议仍负责结束一轮话语。B2 不保留跨请求状态，只在这一整句内部：

1. 用新的 Silero VAD 实例寻找语音段；
2. 为语音段补偿最多 320 ms 前文和 240 ms 后文；
3. 相邻语音段只使用静音间隔的一半，避免互相污染；
4. 忽略短于 0.3 秒的片段；
5. VAD 未找到可用片段时回退到完整话语，避免远场漏检直接丢句；
6. 用固定中文、ITN、4 线程的 SenseVoice FP32 解码。

它不会改变 PC 客户端 VAD，也不会启动第二个 WebSocket ASR 服务。

## 启用与回滚

源码配置模式：

```yaml
selected_module:
  ASR: SherpaSenseVoiceB2
```

管理后台模式：应用迁移并重启 manager-api 后，在智能体 ASR 配置中选择
`Sherpa SenseVoice B2`。

回滚只需把 ASR 重新选择为 `FunASR` 并重启服务。模型目录可以继续保留，
不会影响默认 provider。

## 验证重点

同一批 WAV 必须同时跑 FunASR 基线与 B2，至少比较：

- 20 条中文纯听写；
- 20/50/100 cm 控制录音；
- 20/50/100 cm 客户端真实 ingress；
- 转写结果、单句耗时、100 cm 是否仍误判为英文。
