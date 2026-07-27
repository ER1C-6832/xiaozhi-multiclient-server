import openai
import time
from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.llm.base import LLMProviderBase
from core.providers.llm.openai.client_config import build_openai_transport_config
from core.providers.llm.usage import (
    build_payload_profile,
    create_usage_event,
    emit_usage_event,
)
from urllib.parse import urlparse

TAG = __name__
logger = setup_logging()

# 需要禁用思考模式的平台域名及其对应参数（默认关闭思考模式）
THINKING_DISABLED_DOMAINS = {
    "aliyuncs.com": {"enable_thinking": False},
    "bigmodel.cn": {"thinking": {"type": "disabled"}},
    "moonshot.cn": {"thinking": {"type": "disabled"}},
    "volces.com": {"thinking": {"type": "disabled"}},
}


class LLMProvider(LLMProviderBase):
    supports_usage_context = True

    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.api_key = config.get("api_key")
        if "base_url" in config:
            self.base_url = config.get("base_url")
        else:
            self.base_url = config.get("url")
        
        transport_config = build_openai_transport_config(config)

        param_defaults = {
            "max_tokens": int,
            "temperature": lambda x: round(float(x), 1),
            "top_p": lambda x: round(float(x), 1),
            "frequency_penalty": lambda x: round(float(x), 1),
        }

        for param, converter in param_defaults.items():
            value = config.get(param)
            try:
                setattr(
                    self,
                    param,
                    converter(value) if value not in (None, "") else None,
                )
            except (ValueError, TypeError):
                setattr(self, param, None)

        logger.debug(
            f"意图识别参数初始化: {self.temperature}, {self.max_tokens}, {self.top_p}, {self.frequency_penalty}"
        )

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=transport_config.timeout,
            max_retries=transport_config.max_retries,
        )
        logger.bind(tag=TAG).info(
            f"LLM transport: timeout={transport_config.timeout}, "
            f"max_retries={transport_config.max_retries}"
        )

    @staticmethod
    def normalize_dialogue(dialogue):
        """自动修复 dialogue 中缺失 content 的消息"""
        for msg in dialogue:
            if "role" in msg and "content" not in msg:
                msg["content"] = ""
        return dialogue

    def _apply_thinking_disabled(self, request_params: dict):
        """根据域名自动禁用思考模式"""
        parsed_url = urlparse(self.base_url)
        domain = parsed_url.netloc
        for disabled_domain, params in THINKING_DISABLED_DOMAINS.items():
            if disabled_domain in domain:
                request_params.setdefault("extra_body", {}).update(params)
                logger.bind(tag=TAG).info(f"为域名 {domain} 禁用思考模式，参数: {params}")
                break

    def response(self, session_id, dialogue, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)
        usage_context = kwargs.get("usage_context")
        payload_profile = build_payload_profile(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # 添加可选参数,只有当参数不为None时才添加
        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # 禁用思考模式
        self._apply_thinking_disabled(request_params)

        started_at = time.perf_counter()
        responses = self.client.chat.completions.create(**request_params)

        is_active = True
        final_usage = None
        final_request_id = None
        try:
            for chunk in responses:
                usage_info = getattr(chunk, "usage", None)
                if usage_info is not None:
                    final_usage = usage_info
                    final_request_id = getattr(chunk, "id", None)
                try:
                    delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                    content = getattr(delta, "content", "") if delta else ""
                except IndexError:
                    content = ""
                if content:
                    if "<think>" in content:
                        is_active = False
                        content = content.split("<think>")[0]
                    if "</think>" in content:
                        is_active = True
                        content = content.split("</think>")[-1]
                    if is_active:
                        yield content
        finally:
            responses.close()
            emit_usage_event(
                logger.bind(tag=TAG),
                create_usage_event(
                    usage=final_usage,
                    model=self.model_name,
                    provider="openai_compatible",
                    usage_context=usage_context,
                    payload_profile=payload_profile,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                    request_id=final_request_id,
                    status=(
                        "completed"
                        if final_usage is not None
                        else "usage_unavailable"
                    ),
                ),
                usage_context,
            )

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)
        usage_context = kwargs.get("usage_context")
        payload_profile = build_payload_profile(dialogue, functions)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            "tools": functions,
            "stream_options": {"include_usage": True},
        }

        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # 禁用思考模式
        self._apply_thinking_disabled(request_params)

        started_at = time.perf_counter()
        stream = self.client.chat.completions.create(**request_params)

        final_usage = None
        final_request_id = None
        try:
            for chunk in stream:
                usage_info = getattr(chunk, "usage", None)
                if usage_info is not None:
                    final_usage = usage_info
                    final_request_id = getattr(chunk, "id", None)
                if getattr(chunk, "choices", None):
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", "")
                    tool_calls = getattr(delta, "tool_calls", None)
                    yield content, tool_calls
        finally:
            stream.close()
            emit_usage_event(
                logger.bind(tag=TAG),
                create_usage_event(
                    usage=final_usage,
                    model=self.model_name,
                    provider="openai_compatible",
                    usage_context=usage_context,
                    payload_profile=payload_profile,
                    latency_ms=(time.perf_counter() - started_at) * 1000,
                    request_id=final_request_id,
                    status=(
                        "completed"
                        if final_usage is not None
                        else "usage_unavailable"
                    ),
                ),
                usage_context,
            )
