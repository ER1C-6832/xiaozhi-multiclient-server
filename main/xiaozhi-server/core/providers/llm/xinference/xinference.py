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
        self.base_url = config.get("base_url", "http://localhost:9997")
        # Initialize OpenAI client with Xinference base URL
        # 如果没有v1，增加v1
        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"

        logger.bind(tag=TAG).info(
            f"Initializing Xinference LLM provider with model: {self.model_name}, base_url: {self.base_url}"
        )

        try:
            self.client = OpenAI(
                base_url=self.base_url,
                api_key="xinference",  # Xinference has a similar setup to Ollama where it doesn't need an actual key
            )
            logger.bind(tag=TAG).info("Xinference client initialized successfully")
        except Exception as e:
            logger.bind(tag=TAG).error(f"Error initializing Xinference client: {e}")
            raise

    def response(self, session_id, dialogue, **kwargs):
        logger.bind(tag=TAG).debug(
            f"Sending request to Xinference with model: {self.model_name}, dialogue length: {len(dialogue)}"
        )
        usage_recorder = StreamUsageRecorder(
            logger=logger.bind(tag=TAG),
            model=self.model_name,
            provider="xinference",
            dialogue=dialogue,
            usage_context=kwargs.get("usage_context"),
        )
        responses = self.client.chat.completions.create(
            model=self.model_name, messages=dialogue, stream=True
        )
        is_active = True
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
                        if "<think>" in content:
                            is_active = False
                            content = content.split("<think>")[0]
                        if "</think>" in content:
                            is_active = True
                            content = content.split("</think>")[-1]
                        if is_active:
                            yield content
                except Exception as e:
                    logger.bind(tag=TAG).error(f"Error processing chunk: {e}")
        finally:
            responses.close()
            usage_recorder.emit()

    def response_with_functions(
        self, session_id, dialogue, functions=None, **kwargs
    ):
        logger.bind(tag=TAG).debug(
            f"Sending function call request to Xinference with model: {self.model_name}, dialogue length: {len(dialogue)}"
        )
        if functions:
            logger.bind(tag=TAG).debug(
                f"Function calls enabled with: {[f.get('function', {}).get('name') for f in functions]}"
            )

        usage_recorder = StreamUsageRecorder(
            logger=logger.bind(tag=TAG),
            model=self.model_name,
            provider="xinference",
            dialogue=dialogue,
            tools=functions,
            usage_context=kwargs.get("usage_context"),
        )
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=dialogue,
            stream=True,
            tools=functions,
        )

        try:
            for chunk in stream:
                usage_recorder.capture(chunk)
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                content = delta.content
                tool_calls = delta.tool_calls

                if content:
                    yield content, tool_calls
                elif tool_calls:
                    yield None, tool_calls
        finally:
            stream.close()
            usage_recorder.emit()
