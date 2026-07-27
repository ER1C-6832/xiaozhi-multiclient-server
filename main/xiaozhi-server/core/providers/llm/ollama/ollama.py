from config.logger import setup_logging
from openai import OpenAI
import json
from core.providers.llm.base import LLMProviderBase
from core.providers.llm.usage import StreamUsageRecorder

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    supports_usage_context = True

    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.base_url = config.get("base_url", "http://localhost:11434")
        # Initialize OpenAI client with Ollama base URL
        # 如果没有v1，增加v1
        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"

        self.client = OpenAI(
            base_url=self.base_url,
            api_key="ollama",  # Ollama doesn't need an API key but OpenAI client requires one
        )

        # 检查是否是qwen3模型
        self.is_qwen3 = self.model_name and self.model_name.lower().startswith("qwen3")

    def response(self, session_id, dialogue, **kwargs):
        # 如果是qwen3模型，在用户最后一条消息中添加/no_think指令
        if self.is_qwen3:
            # 复制对话列表，避免修改原始对话
            dialogue_copy = dialogue.copy()

            # 找到最后一条用户消息
            for i in range(len(dialogue_copy) - 1, -1, -1):
                if dialogue_copy[i]["role"] == "user":
                    # 在用户消息前添加/no_think指令
                    dialogue_copy[i]["content"] = (
                        "/no_think " + dialogue_copy[i]["content"]
                    )
                    logger.bind(tag=TAG).debug(f"为qwen3模型添加/no_think指令")
                    break

            # 使用修改后的对话
            dialogue = dialogue_copy

        usage_recorder = StreamUsageRecorder(
            logger=logger.bind(tag=TAG),
            model=self.model_name,
            provider="ollama",
            dialogue=dialogue,
            usage_context=kwargs.get("usage_context"),
        )
        responses = self.client.chat.completions.create(
            model=self.model_name,
            messages=dialogue,
            stream=True,
            stream_options={"include_usage": True},
        )
        is_active = True
        # 用于处理跨chunk的标签
        buffer = ""

        try:
            for chunk in responses:
                usage_recorder.capture(chunk)
                try:
                    delta = (
                        chunk.choices[0].delta
                        if getattr(chunk, "choices", None)
                        else None
                    )
                    content = delta.content if hasattr(delta, "content") else ""

                    if content:
                        # 将内容添加到缓冲区
                        buffer += content

                        # 处理缓冲区中的标签
                        while "<think>" in buffer and "</think>" in buffer:
                            # 找到完整的<think></think>标签并移除
                            pre = buffer.split("<think>", 1)[0]
                            post = buffer.split("</think>", 1)[1]
                            buffer = pre + post

                        # 处理只有开始标签的情况
                        if "<think>" in buffer:
                            is_active = False
                            buffer = buffer.split("<think>", 1)[0]

                        # 处理只有结束标签的情况
                        if "</think>" in buffer:
                            is_active = True
                            buffer = buffer.split("</think>", 1)[1]

                        # 如果当前处于活动状态且缓冲区有内容，则输出
                        if is_active and buffer:
                            yield buffer
                            buffer = ""  # 清空缓冲区

                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing chunk: {e}")
        finally:
            responses.close()
            usage_recorder.emit()

    def response_with_functions(
        self, session_id, dialogue, functions=None, **kwargs
    ):
        # 如果是qwen3模型，在用户最后一条消息中添加/no_think指令
        if self.is_qwen3:
            # 复制对话列表，避免修改原始对话
            dialogue_copy = dialogue.copy()

            # 找到最后一条用户消息
            for i in range(len(dialogue_copy) - 1, -1, -1):
                if dialogue_copy[i]["role"] == "user":
                    # 在用户消息前添加/no_think指令
                    dialogue_copy[i]["content"] = (
                        "/no_think " + dialogue_copy[i]["content"]
                    )
                    logger.bind(tag=TAG).debug(f"为qwen3模型添加/no_think指令")
                    break

            # 使用修改后的对话
            dialogue = dialogue_copy

        usage_recorder = StreamUsageRecorder(
            logger=logger.bind(tag=TAG),
            model=self.model_name,
            provider="ollama",
            dialogue=dialogue,
            tools=functions,
            usage_context=kwargs.get("usage_context"),
        )
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=dialogue,
            stream=True,
            tools=functions,
            stream_options={"include_usage": True},
        )

        is_active = True
        buffer = ""

        try:
            for chunk in stream:
                usage_recorder.capture(chunk)
                try:
                    delta = (
                        chunk.choices[0].delta
                        if getattr(chunk, "choices", None)
                        else None
                    )
                    content = delta.content if hasattr(delta, "content") else None
                    tool_calls = (
                        delta.tool_calls if hasattr(delta, "tool_calls") else None
                    )

                    # 如果是工具调用，直接传递
                    if tool_calls:
                        yield None, tool_calls
                        continue

                    # 处理文本内容
                    if content:
                        # 将内容添加到缓冲区
                        buffer += content

                        # 处理缓冲区中的标签
                        while "<think>" in buffer and "</think>" in buffer:
                            # 找到完整的<think></think>标签并移除
                            pre = buffer.split("<think>", 1)[0]
                            post = buffer.split("</think>", 1)[1]
                            buffer = pre + post

                        # 处理只有开始标签的情况
                        if "<think>" in buffer:
                            is_active = False
                            buffer = buffer.split("<think>", 1)[0]

                        # 处理只有结束标签的情况
                        if "</think>" in buffer:
                            is_active = True
                            buffer = buffer.split("</think>", 1)[1]

                        # 如果当前处于活动状态且缓冲区有内容，则输出
                        if is_active and buffer:
                            yield buffer, None
                            buffer = ""  # 清空缓冲区
                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing function chunk: {e}")
                    continue
        finally:
            stream.close()
            usage_recorder.emit()
