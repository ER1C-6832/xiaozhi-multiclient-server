"""Cost-aware local routing for function-call requests.

The router intentionally uses no model call.  It selects a small candidate set
from tool metadata for explicit tool intents, and otherwise chooses the
tool-free chat path.  False negatives are safer and cheaper than exposing every
tool schema to an ambiguous request.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class ToolRouteDecision:
    route: str
    reason: str
    candidates: List[Dict[str, Any]]
    available_tool_count: int
    candidate_schema_chars: int


_DEVICE_TOOL_READINESS_REASONS = frozenset(
    {
        "domain_without_candidate",
        "exact_title_lookup",
        "implicit_note_keyword_search",
        "note_keyword_search",
    }
)
_REQUIRED_SINGLE_TOOL_REASONS = frozenset(
    {
        "exact_title_lookup",
        "implicit_note_keyword_search",
        "note_keyword_search",
    }
)


def should_wait_for_device_tools(decision: ToolRouteDecision) -> bool:
    """Return whether chat fallback may only reflect an unfinished MCP list."""

    return (
        decision.route == "chat"
        and not decision.candidates
        and decision.reason in _DEVICE_TOOL_READINESS_REASONS
    )


def required_tool_choice(decision: ToolRouteDecision) -> Dict[str, Any] | None:
    """Force deterministic read routes instead of letting the model decline."""

    if (
        decision.route != "tool"
        or decision.reason not in _REQUIRED_SINGLE_TOOL_REASONS
        or len(decision.candidates) != 1
    ):
        return None
    name = _tool_name(decision.candidates[0])
    if not name:
        return None
    return {"type": "function", "function": {"name": name}}


_DOMAIN_PATTERNS = {
    "notes": re.compile(
        r"便签|笔记|待办|备忘|记录|记一下|记下来|标签|回收站|置顶|"
        r"改内容|修改内容|改正文|修改正文|改标题|修改标题|"
        r"这条|那条|改成|换成"
    ),
    "ui": re.compile(r"界面|页面|打开|显示|切到|回到|搜索框"),
    "confirmation": re.compile(r"确认|取消|拒绝|待确认"),
    "weather": re.compile(r"天气|气温|温度|下雨|降雨|刮风|空气质量"),
    "news": re.compile(r"新闻|热搜|资讯|头条"),
    "music": re.compile(r"音乐|歌曲|唱歌|播放|放一首|来一首"),
    "lunar": re.compile(r"农历|阴历|黄历"),
    "exit": re.compile(r"再见|拜拜|退出|退下|待机|晚安"),
}

_NAME_DOMAINS = {
    "notes_": "notes",
    "tags_": "notes",
    "ui_": "ui",
    "assistant_": "confirmation",
    "get_weather": "weather",
    "get_news": "news",
    "play_music": "music",
    "get_lunar": "lunar",
    "handle_exit": "exit",
}

_ACTION_HINTS = {
    "create": re.compile(r"创建|新建|新增|添加|加个|记个|记一下|记下来|记录"),
    "search": re.compile(r"搜索|查找|找一下|找找|查询|看看"),
    "resolve": re.compile(r"刚才那|那一条|那条|哪个|定位"),
    "list": re.compile(r"列出|有哪些|最近|全部|列表|看看"),
    "get": re.compile(r"读取|读一下|详情|内容|编号"),
    "append": re.compile(r"追加|补充|补一句|后面加"),
    "update_title": re.compile(r"改名|改标题|标题改"),
    "replace": re.compile(
        r"替换|覆盖|全部改成|正文改|改内容|修改内容|改正文|修改正文|"
        r"(?:这条|那条|内容|正文|便签).{0,12}(?:改成|换成)|"
        r".{0,12}的那条.{0,8}(?:改成|换成)"
    ),
    "delete": re.compile(r"删除|删掉|移除"),
    "restore": re.compile(r"恢复|还原|找回"),
    "pin": re.compile(r"置顶|取消置顶"),
    "show": re.compile(r"打开|显示|切到|回到"),
    "confirm": re.compile(r"确认|同意|执行"),
    "reject": re.compile(r"取消|拒绝|不要了"),
}

_CLARIFICATION_PATTERN = re.compile(
    r"请.{0,12}(告诉|提供|补充|说明|选择)|"
    r"(需要|还缺|缺少).{0,12}(什么|哪些|哪一|信息|内容|标题|地点|城市)|"
    r"(什么|哪些|哪一|哪个|哪里|几号|几点|是否).{0,8}[？?]?$|"
    r"[？?]$"
)

_EXACT_TITLE_LOOKUP_PATTERN = re.compile(
    r"(?:搜索|查找|查询|找一下|找找|看看).{0,12}"
    r"标题(?:为|是|叫|名为)"
)

_NOTE_KEYWORD_SEARCH_PATTERN = re.compile(
    r"(?:搜索|查找|查询|找一下|找找|看看|查).{0,20}(?:便签|笔记)|"
    r"(?:便签|笔记).{0,20}(?:搜索|查找|查询|找一下|找找|看看|查)"
)

_IMPLICIT_RELATED_NOTE_PATTERN = re.compile(
    r"^[\w\u4e00-\u9fff]{1,24}(?:相关|有关)(?:的)?(?:便签|笔记)?[。！!？?\s]*$"
)

_MUTATION_PATTERN = re.compile(
    r"删除|删掉|移除|恢复|还原|找回|追加|补充|改成|换成|"
    r"修改|替换|覆盖|置顶|绑定"
)


def _tool_name(tool: Dict[str, Any]) -> str:
    function = tool.get("function", {}) if isinstance(tool, dict) else {}
    return str(function.get("name") or "")


def _tool_description(tool: Dict[str, Any]) -> str:
    function = tool.get("function", {}) if isinstance(tool, dict) else {}
    return str(function.get("description") or "")


def _domain_for_name(name: str) -> str | None:
    for prefix, domain in _NAME_DOMAINS.items():
        if name.startswith(prefix):
            return domain
    return None


def _query_domains(query: str) -> set[str]:
    return {
        domain
        for domain, pattern in _DOMAIN_PATTERNS.items()
        if pattern.search(query)
    }


def _bigrams(value: str) -> set[str]:
    compact = re.sub(r"[\W_]+", "", value.lower(), flags=re.UNICODE)
    return {
        compact[index : index + 2]
        for index in range(max(0, len(compact) - 1))
    }


def _score(query: str, tool: Dict[str, Any], domains: set[str]) -> int:
    name = _tool_name(tool)
    description = _tool_description(tool)
    domain = _domain_for_name(name)
    score = 20 if domain in domains else 0

    normalized_name = name.replace(".", "_").lower()
    for action, pattern in _ACTION_HINTS.items():
        if pattern.search(query) and action in normalized_name:
            score += 14

    query_bigrams = _bigrams(query)
    if query_bigrams:
        overlap = query_bigrams.intersection(_bigrams(description))
        score += min(18, len(overlap) * 3)
    return score


def select_candidate_tools(
    query: str,
    tools: Iterable[Dict[str, Any]],
    *,
    previous_query: str = "",
    previous_assistant: str = "",
    max_candidates: int = 5,
    max_schema_chars: int = 5_000,
) -> ToolRouteDecision:
    available = list(tools or [])
    normalized_query = (query or "").strip().lower()
    domains = _query_domains(normalized_query)
    continuation = False
    implicit_note_search = bool(
        normalized_query
        and "标签" not in normalized_query
        and not domains
        and _IMPLICIT_RELATED_NOTE_PATTERN.match(normalized_query)
    )
    if implicit_note_search:
        domains = {"notes"}

    if (
        normalized_query
        and not domains
        and _CLARIFICATION_PATTERN.search((previous_assistant or "").strip())
    ):
        previous_domains = _query_domains((previous_query or "").strip().lower())
        if previous_domains:
            domains = previous_domains
            normalized_query = f"{previous_query} {query}".strip().lower()
            continuation = True

    if not normalized_query:
        candidates: List[Dict[str, Any]] = []
        reason = "empty_query"
    elif not domains:
        candidates = []
        reason = "no_explicit_tool_domain"
    else:
        exact_title_lookup = bool(
            _EXACT_TITLE_LOOKUP_PATTERN.search(normalized_query)
            and not _MUTATION_PATTERN.search(normalized_query)
        )
        if exact_title_lookup:
            resolver = next(
                (
                    tool
                    for tool in available
                    if _tool_name(tool) == "notes_resolve"
                ),
                None,
            )
            candidates = [resolver] if resolver is not None else []
            reason = (
                "exact_title_lookup"
                if candidates
                else "domain_without_candidate"
            )
            schema_chars = (
                len(
                    json.dumps(
                        candidates,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                )
                if candidates
                else 0
            )
            return ToolRouteDecision(
                route="tool" if candidates else "chat",
                reason=reason,
                candidates=candidates,
                available_tool_count=len(available),
                candidate_schema_chars=schema_chars,
            )
        note_keyword_search = bool(
            "标签" not in normalized_query
            and not _MUTATION_PATTERN.search(normalized_query)
            and (
                implicit_note_search
                or _NOTE_KEYWORD_SEARCH_PATTERN.search(normalized_query)
            )
        )
        if note_keyword_search:
            search = next(
                (
                    tool
                    for tool in available
                    if _tool_name(tool) == "notes_search"
                ),
                None,
            )
            candidates = [search] if search is not None else []
            schema_chars = (
                len(
                    json.dumps(
                        candidates,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                )
                if candidates
                else 0
            )
            return ToolRouteDecision(
                route="tool" if candidates else "chat",
                reason=(
                    "implicit_note_keyword_search"
                    if implicit_note_search
                    else "note_keyword_search"
                ),
                candidates=candidates,
                available_tool_count=len(available),
                candidate_schema_chars=schema_chars,
            )
        ranked = sorted(
            (
                (_score(normalized_query, tool, domains), index, tool)
                for index, tool in enumerate(available)
                if _domain_for_name(_tool_name(tool)) in domains
            ),
            key=lambda item: (-item[0], item[1]),
        )
        candidates = []
        for score, _, tool in ranked:
            if score < 20 or len(candidates) >= max(1, int(max_candidates)):
                continue
            proposed = [*candidates, tool]
            proposed_chars = len(
                json.dumps(
                    proposed,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
            if proposed_chars <= max(0, int(max_schema_chars)):
                candidates.append(tool)
        if candidates:
            reason = (
                "tool_parameter_continuation"
                if continuation
                else "explicit_tool_candidates"
            )
        else:
            reason = "domain_without_candidate"

    schema_chars = (
        len(
            json.dumps(
                candidates,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        )
        if candidates
        else 0
    )
    return ToolRouteDecision(
        route="tool" if candidates else "chat",
        reason=reason,
        candidates=candidates,
        available_tool_count=len(available),
        candidate_schema_chars=schema_chars,
    )


def parse_plain_tool_call(
    content: str, tools: Iterable[Dict[str, Any]]
) -> Dict[str, Any] | None:
    """Parse provider fallbacks such as ``notes_search{"query":"x"}``.

    Only names from the already-authorized candidate set are accepted. Natural
    language and trailing text are rejected, so malformed provider output is
    never executed as an arbitrary tool request.
    """
    value = (content or "").strip()
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.]*)\s*(\{.*\})", value, re.S)
    if match is None:
        return None
    allowed = {_tool_name(tool) for tool in tools or []}
    name = match.group(1)
    if name not in allowed:
        return None
    try:
        arguments = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    if not isinstance(arguments, dict):
        return None
    return {"name": name, "arguments": arguments}
