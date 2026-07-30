"""设备端MCP工具执行器"""

import re
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

_ID_MUTATING_TOOLS = {
    "notes_append",
    "notes_update_title",
    "notes_replace_content",
    "notes_convert_type",
    "notes_pin",
    "notes_delete",
    "notes_restore",
    "tags_bind",
}
_ID_READ_TOOLS = {"notes_get", "ui_open_note"}
_ID_CONSUMING_TOOLS = _ID_MUTATING_TOOLS | _ID_READ_TOOLS
_TARGET_PRODUCER_TOOLS = {
    "notes_resolve",
    "notes_search",
    "notes_list_recent",
    "notes_list_by_tag",
    "notes_list_deleted",
    "notes_list_todos",
    "notes_list_pinned",
    "notes_create",
}


def _requested_note_ids(arguments: Dict[str, Any]) -> set[int]:
    values = []
    if "note_id" in arguments:
        values.append(arguments.get("note_id"))
    note_ids = arguments.get("note_ids")
    if isinstance(note_ids, list):
        values.extend(note_ids)
    result = set()
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            note_id = int(value)
        except (TypeError, ValueError):
            continue
        if note_id > 0:
            result.add(note_id)
    return result


def _affected_note_ids(payload: dict) -> set[int]:
    values = payload.get("affected_note_ids")
    if not isinstance(values, list):
        return set()
    return _requested_note_ids({"note_ids": values})


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


def _normalize_exact_title(value: Any) -> str:
    title = str(value or "").strip()
    title = re.sub(r"^标题(?:为|是|叫|名为)?\s*", "", title)
    title = re.sub(r"\s*的(?:便签|笔记)?$", "", title)
    return title.strip("“”\"' ")


def _result_notes(payload: dict) -> list[dict]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    notes = result.get("notes")
    if not isinstance(notes, list):
        notes = result.get("candidates")
    return [item for item in (notes or []) if isinstance(item, dict)]


def _safe_read_summary(tool_name: str, payload: dict) -> str:
    notes = _result_notes(payload)
    if not notes:
        return str(payload.get("message") or "没有找到符合条件的便签。")
    rendered = []
    for note in notes[:5]:
        title = str(note.get("title") or "未命名").strip()
        snippet = str(note.get("snippet") or "").strip()
        rendered.append(
            f"标题“{title}”" + (f"，内容“{snippet}”" if snippet else "")
        )
    prefix = "找到一条便签：" if len(notes) == 1 else f"找到{len(notes)}条便签："
    return prefix + "；".join(rendered) + "。"


class DeviceMCPExecutor(ToolExecutor):
    """设备端MCP工具执行器"""

    def __init__(self, conn):
        self.conn = conn

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行设备端MCP工具"""
        arguments = dict(arguments or {})
        if tool_name == "notes_resolve" and "exact_title" in arguments:
            normalized = _normalize_exact_title(arguments.get("exact_title"))
            if normalized:
                arguments["exact_title"] = normalized

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

        requested_ids = _requested_note_ids(arguments)
        if tool_name in _ID_CONSUMING_TOOLS:
            provenance = getattr(conn, "_mcp_target_provenance", {})
            required_source = (
                "resolved" if tool_name in _ID_MUTATING_TOOLS else "readable"
            )
            unauthorized = {
                note_id
                for note_id in requested_ids
                if provenance.get(note_id) not in {required_source, "resolved"}
            }
            if not requested_ids or unauthorized:
                logger.bind(tag=TAG).warning(
                    "Blocked untrusted note-id tool call: "
                    f"tool={tool_name}, requested={sorted(requested_ids)}, "
                    f"unauthorized={sorted(unauthorized)}"
                )
                return ActionResponse(
                    action=Action.RESPONSE,
                    response=(
                        "未执行：目标尚未按便签标题唯一定位，请先按标题查找。"
                    ),
                    execution_succeeded=False,
                    workflow_terminal=True,
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

                if status == "success" and tool_name in _TARGET_PRODUCER_TOOLS:
                    affected_ids = _affected_note_ids(resultJson)
                    if affected_ids:
                        provenance = dict(
                            getattr(conn, "_mcp_target_provenance", {})
                        )
                        source = (
                            "resolved"
                            if tool_name == "notes_resolve"
                            and isinstance(result_payload, dict)
                            and result_payload.get("resolution_status") != "ambiguous"
                            and len(affected_ids) == 1
                            else "readable"
                        )
                        for note_id in affected_ids:
                            provenance[note_id] = source
                        conn._mcp_target_provenance = provenance

                if requires_confirmation or status == "requires_confirmation":
                    if tool_name in _ID_MUTATING_TOOLS:
                        conn._mcp_target_provenance = {}
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
                        execution_succeeded=False,
                        workflow_terminal=True,
                    )
                if status in {"failed", "blocked", "rejected"}:
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=message or "设备工具操作未完成。",
                        execution_succeeded=False,
                        workflow_terminal=True,
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
                            execution_succeeded=False,
                            workflow_terminal=False,
                        )
                if status == "success" and tool_name in _MUTATING_TOOLS:
                    if tool_name in _ID_MUTATING_TOOLS:
                        conn._mcp_target_provenance = {}
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=message or "设备工具操作已完成。",
                        execution_succeeded=True,
                        workflow_terminal=True,
                    )
                if status == "success" and tool_name in {
                    "notes_search",
                    "notes_list_recent",
                    "notes_list_by_tag",
                    "notes_list_deleted",
                    "notes_list_todos",
                    "notes_list_pinned",
                    "notes_get",
                }:
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=_safe_read_summary(tool_name, resultJson),
                        execution_succeeded=True,
                        workflow_terminal=True,
                    )
                if status == "success" and tool_name == "notes_resolve":
                    workflow = getattr(conn, "_pending_tool_workflow", None)
                    if getattr(workflow, "operation", None) in {
                        "delete",
                        "restore",
                        "replace_content",
                        "update_title",
                        "append",
                        "pin",
                        "get",
                    }:
                        return ActionResponse(
                            action=Action.REQLLM,
                            result=json.dumps(resultJson, ensure_ascii=False),
                            execution_succeeded=True,
                            workflow_terminal=False,
                        )
                    return ActionResponse(
                        action=Action.RESPONSE,
                        response=_safe_read_summary(tool_name, resultJson),
                        execution_succeeded=True,
                        workflow_terminal=True,
                    )

            return ActionResponse(
                action=Action.REQLLM,
                result=str(result),
                execution_succeeded=(
                    status == "success" if isinstance(resultJson, dict) else None
                ),
                workflow_terminal=None,
            )

        except ValueError as e:
            return ActionResponse(
                action=Action.NOTFOUND,
                response=str(e),
                execution_succeeded=False,
                workflow_terminal=True,
            )
        except Exception as e:
            logger.bind(tag=TAG).error(
                f"设备端工具调用失败: tool={tool_name}, error={type(e).__name__}: {e}"
            )
            return ActionResponse(
                action=Action.ERROR,
                response="设备工具操作暂时没有完成，请稍后重试。",
                execution_succeeded=False,
                workflow_terminal=True,
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
