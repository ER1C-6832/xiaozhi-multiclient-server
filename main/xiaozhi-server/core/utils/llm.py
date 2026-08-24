import os
import sys
import threading

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.insert(0, project_root)

from config.logger import setup_logging
import importlib

logger = setup_logging()

_provider_import_lock = threading.RLock()


def create_instance(class_name, *args, **kwargs):
    # 创建LLM实例
    if os.path.exists(os.path.join('core', 'providers', 'llm', class_name, f'{class_name}.py')):
        lib_name = f'core.providers.llm.{class_name}.{class_name}'
        # Do not treat membership in sys.modules as proof that module execution
        # has completed. Import machinery inserts a module there before running
        # its body, so another Session initialization thread could otherwise
        # observe a half-initialized provider without LLMProvider.
        with _provider_import_lock:
            provider_module = importlib.import_module(lib_name)
            provider_class = getattr(provider_module, "LLMProvider", None)
            if provider_class is None:
                raise ImportError(
                    f"LLM provider module did not finish initialization: {lib_name}"
                )
        return provider_class(*args, **kwargs)

    raise ValueError(f"不支持的LLM类型: {class_name}，请检查该配置的type是否设置正确")
