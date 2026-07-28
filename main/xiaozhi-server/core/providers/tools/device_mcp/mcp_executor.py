"""设备端MCP工具执行器"""

from typing import Dict, Any, TYPE_CHECKING
from config.logger import setup_logging

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from ..base import ToolType, ToolDefinition, ToolExecutor
from plugins_func.register import Action, ActionResponse
from .mcp_handler import call_mcp_tool

TAG = __name__
logger = setup_logging()

_MUTATING_TOOLS = {
    "notes_create",
    "notes_append",
    "notes_update_title",
    "notes_replace_content",
    "notes_convert_type",
    "notes_pin",
    "notes_delete",
    "notes_restore",
    "tags_create",
    "tags_delete",
    "tags_bind",
    "assistant_confirm",
    "assistant_reject",
}


def _candidate_titles(payload: dict) -> list[str]:
    result = payload.get("result")
    candidates = result.get("candidates") if isinstance(result, dict) else None
    if not isinstance(candidates, list):
        return []
    return [
        str(candidate.get("title")).strip()
        for candidate in candidates[:5]
        if isinstance(candidate, dict) and str(candidate.get("title") or "").strip()
    ]


class DeviceMCPExecutor(ToolExecutor):
    """设备端MCP工具执行器"""

    def __init__(self, conn):
        self.conn = conn

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行设备端MCP工具"""
        if not hasattr(conn, "mcp_client") or not conn.mcp_client:
            return ActionResponse(
                action=Action.ERROR,
                response="设备端MCP客户端未初始化",
            )

        if not await conn.mcp_client.is_ready():
            return ActionResponse(
                action=Action.ERROR,
                response="设备端MCP客户端未准备就绪",
            )

        try:
            # 转换参数为JSON字符串
            import json

            args_str = json.dumps(arguments) if arguments else "{}"

            # 调用设备端MCP工具
            result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)

            resultJson = None
            if isinstance(result, str):
                try:
                    resultJson = json.loads(result)
                except Exception as e:
                    pass

            # 视觉大模型不经过二次LLM处理
            if (
                resultJson is not None
                and isinstance(resultJson, dict)
                and "action" in resultJson
            ):
                return ActionResponse(
                    action=Action[resultJson["action"]],
                    response=resultJson.get("response", ""),
                )

            if isinstance(resultJson, dict):
                status = str(resultJson.get("status") or "")
                message = str(resultJson.get("message") or "").strip()
                requires_confirmation = bool(
                    resultJson.get("requires_confirmation")
                )
                result_payload = resultJson.get("result")

                if requires_confirmation or status == "requires_confirmation":
                    confirmation_id = str(
                        resultJson.get("confirmation_id") or ""
                    ).strip()
                    confirmation_displayed = False
                    if confirmation_id and conn.mcp_client.has_tool(
                        "ui_show_confirmation"
                    ):
                        try:
                            await call_mcp_tool(
                                conn,
                                conn.mcp_client,
                                "ui_show_confirmation",
                                json.dumps(
                                    {"confirmation_id": confirmation_id},
                                    ensure_ascii=False,
                                ),
                            )
                            confirmation_displayed = True
                        except Exception as display_error:
                            logger.bind(tag=TAG).warning(
                                "待确认操作已创建，但确认卡片展示失败: "
                                f"{type(display_error).__name__}: {display_error}"
                            )
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=(
                            f"{message or '该操作需要确认'}，请在客户端确认卡片中确认。"
                            if confirmation_displayed
                            else f"{message or '该操作需要确认'}，但确认卡片暂时无法显示。"
                        ),
                    )
                if status in {"failed", "blocked", "rejected"}:
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=message or "设备工具操作未完成。",
                    )
                if tool_name == "notes_resolve" and isinstance(result_payload, dict):
                    if result_payload.get("resolution_status") == "ambiguous":
                        titles = _candidate_titles(resultJson)
                        candidate_text = "、".join(titles)
                        return ActionResponse(
                            action=Action.RESPONSE,
                            response=(
                                f"找到多条候选：{candidate_text}。请直接说完整标题。"
                                if candidate_text
                                else "找到多条候选，请直接说完整标题。"
                            ),
                        )
                if status == "success" and tool_name in _MUTATING_TOOLS:
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=message or "设备工具操作已完成。",
                    )

            return ActionResponse(action=Action.REQLLM, result=str(result))

        except ValueError as e:
            return ActionResponse(action=Action.NOTFOUND, response=str(e))
        except Exception as e:
            logger.bind(tag=TAG).error(
                f"设备端工具调用失败: tool={tool_name}, error={type(e).__name__}: {e}"
            )
            return ActionResponse(
                action=Action.ERROR,
                response="设备工具操作暂时没有完成，请稍后重试。",
            )

    def get_tools(self) -> Dict[str, ToolDefinition]:
        """获取所有设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return {}

        tools = {}
        mcp_tools = self.conn.mcp_client.get_available_tools()

        for tool in mcp_tools:
            func_def = tool.get("function", {})
            tool_name = func_def.get("name", "")

            if tool_name:
                tools[tool_name] = ToolDefinition(
                    name=tool_name, description=tool, tool_type=ToolType.DEVICE_MCP
                )

        return tools

    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定的设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return False

        return self.conn.mcp_client.has_tool(tool_name)
