# LLM Token Budget Control — Phase 3 Delivery

## Outcome

Phase 3 adds explicit provider-neutral budget enforcement on top of the Phase 1
and Phase 2 usage events. The server now prevents unbounded recursive LLM/tool
chains, records every authorization or rejection as structured data, and sends
one safe turn summary to compatible clients.

The PC client exposes the result under **Developer 诊断 → Token 与预算**.

## Active policy

The standard B2 configuration enables:

```yaml
token_budget:
  enabled: true
  max_total_tokens_per_turn: 12000
  max_llm_calls_per_turn: 3
  max_tool_calls_per_turn: 8
  max_output_tokens_per_request: 200
  warn_at_percent: 80
  client_telemetry: true
```

Deployments can override only this section in `data/.config.yaml`. API-managed
deployments retain the locally merged policy instead of silently losing it when
agent/model configuration is fetched from manager-api.

## Enforcement semantics

### Hard limits

- No more than three LLM requests may start in one user turn. The initial call
  and every tool-result follow-up both count.
- No more than eight real tools may execute in one turn. A parallel batch that
  would cross the limit is rejected as a whole before any tool in that batch
  executes.
- Once provider-reported known Tokens reach 12,000, no later LLM request or tool
  batch starts.
- OpenAI-compatible adapters, including the current GLM and Qwen entries, receive
  an effective `max_tokens` of the smaller model setting or 200.

### Explicit limitations

Provider Token usage arrives after a response. The first request's exact input
cannot be pre-counted consistently across providers. It may therefore cross the
turn limit; the event becomes `limit_reached_after_response` and all subsequent
costly or mutating work is blocked.

If a provider does not return usage, cumulative Token enforcement is marked
incomplete. LLM-call and tool-call hard limits still apply.

Adapters that cannot accept a per-request output override are labelled
`output_cap_enforced=false`; the client never claims that their output hard cap
was applied.

## Structured logs

Existing events remain:

```text
TOKEN_USAGE {"event":"llm_usage",...}
TOKEN_USAGE {"event":"tool_usage",...}
TOKEN_USAGE {"event":"turn_usage_summary",...}
```

Phase 3 adds:

```text
TOKEN_BUDGET {"event":"token_budget","action":"request_authorized",...}
TOKEN_BUDGET {"event":"token_budget","action":"usage_observed",...}
TOKEN_BUDGET {"event":"token_budget","action":"request_blocked",...}
TOKEN_BUDGET {"event":"token_budget","action":"tool_batch_blocked",...}
```

No event contains prompt text, API keys, tool arguments, or tool results.

## Client telemetry contract

At turn completion, the server sends:

```json
{
  "type": "token_usage",
  "session_id": "...",
  "usage": {
    "model": "glm-4.7-flash",
    "known_total_tokens": 1419,
    "max_total_tokens_per_turn": 12000,
    "budget_status": "within_budget"
  }
}
```

The real payload uses a strict allowlist containing only identifiers, counts,
limits, duration, status, and capability flags. The PC client validates every
numeric and Boolean field, rejects malformed payloads, and ignores stale
connection generations or foreign sessions.

## User-visible rejection

Budget enforcement never silently truncates a tool chain:

- LLM-call limit: “本轮大模型调用已达到预算上限，请开始新一轮对话。”
- Tool-count limit: “本轮工具调用数量超过预算限制，请缩小操作范围后重试。”
- Known Token limit: “本轮 Token 已达到预算上限，未继续执行工具，请开始新一轮对话。”

The same reason is present in `budget_reason` and the Developer panel.

## Verification

- Server compilation passes.
- Server target regression: 25 tests.
- Client protocol, state, transport-adjacent, QML architecture, Developer
  ViewModel, text-turn, and preferences regression: 57 tests.
- Both repositories pass `git diff --check`.

