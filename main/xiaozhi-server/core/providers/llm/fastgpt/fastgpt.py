import json
from config.logger import setup_logging
import requests
from core.providers.llm.base import LLMProviderBase
from core.utils.util import check_model_key
from core.providers.llm.usage import StreamUsageRecorder

TAG = __name__
logger = setup_logging()


class LLMProvider(LLMProviderBase):
    supports_usage_context = True

    def __init__(self, config):
        self.api_key = config["api_key"]
        self.base_url = config.get("base_url")
        self.detail = config.get("detail", False)
        self.variables = config.get("variables", {})
        model_key_msg = check_model_key("FastGPTLLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

    def response(self, session_id, dialogue, **kwargs):
        # 取最后一条用户消息
        last_msg = next(m for m in reversed(dialogue) if m["role"] == "user")
        usage_recorder = StreamUsageRecorder(
            logger=logger.bind(tag=TAG),
            model="fastgpt_application",
            provider="fastgpt",
            dialogue=[last_msg],
            usage_context=kwargs.get("usage_context"),
        )

        # 发起流式请求
        try:
            with requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "stream": True,
                    "chatId": session_id,
                    "detail": self.detail,
                    "variables": self.variables,
                    "messages": [{"role": "user", "content": last_msg["content"]}],
                },
                stream=True,
            ) as r:
                for line in r.iter_lines():
                    if line:
                        try:
                            if not line.startswith(b"data: "):
                                continue
                            if line[6:].decode("utf-8") == "[DONE]":
                                break

                            data = json.loads(line[6:])
                            usage_recorder.capture(data)
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                if (
                                    delta
                                    and "content" in delta
                                    and delta["content"] is not None
                                ):
                                    content = delta["content"]
                                    if "<think>" in content:
                                        continue
                                    if "</think>" in content:
                                        continue
                                    yield content

                        except json.JSONDecodeError:
                            continue
                        except Exception:
                            continue
        finally:
            usage_recorder.emit()

    def response_with_functions(
        self, session_id, dialogue, functions=None, **kwargs
    ):
        logger.bind(tag=TAG).error(
            f"fastgpt暂未实现完整的工具调用（function call），建议使用其他意图识别"
        )
        usage_recorder = StreamUsageRecorder(
            logger=logger.bind(tag=TAG),
            model="fastgpt_application",
            provider="fastgpt",
            dialogue=dialogue,
            tools=functions,
            usage_context=kwargs.get("usage_context"),
        )
        usage_recorder.emit("unsupported_function_call")
        if False:
            yield None, None
