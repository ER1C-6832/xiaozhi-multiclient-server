# LLM Token Usage Logging — Phase 2 Delivery

## Outcome

Phase 2 extends Phase 1 Token observability from the OpenAI-compatible adapter
to every LLM adapter currently shipped by the server. All adapters now emit the
same safe request event and participate in the same per-turn aggregation.

This phase is observation only. It does not change the selected model, prompts,
tool routing, generation limits, credentials, or billing behavior.

## Common event contract

Every provider call emits one `TOKEN_USAGE` JSON event containing:

- turn, session, request, call index, purpose, provider, and model identifiers;
- provider-reported input, output, total, cached, reasoning, and tool-input
  Token counts where available;
- request latency and completion status;
- message and tool-schema sizes, measured only as character counts.

No prompt text, API key, tool arguments, tool result, or conversation content is
written into these events.

At the end of a user turn, `turn_usage_summary` aggregates all recursive LLM and
tool calls. If any provider request lacks a Token count, the corresponding turn
total remains `null`; the server does not fabricate an exact number from text
length.

## Provider support matrix

| Adapter | Reporting behavior | Confidence |
|---|---|---|
| OpenAI-compatible, including Qwen/GLM/DeepSeek/Doubao | Requests final stream usage and records provider totals | Exact when the endpoint returns usage |
| Ollama OpenAI-compatible endpoint | Requests `include_usage` and records final stream usage | Exact on supported Ollama versions |
| Gemini | Reads `usage_metadata`, including cached, thought, and tool-prompt counts | Exact when metadata is returned |
| AliBL / DashScope Application | Reads the SDK response `usage` object | Exact when the application response exposes usage |
| Xinference | Reads compatible usage but converts negative placeholder counts to unknown | Conditional |
| Dify | Searches terminal SSE metadata for a usage object | Conditional on Dify/app response |
| FastGPT | Searches terminal SSE payloads for a usage object | Conditional on deployment response |
| Coze | Searches SDK events for usage metadata | Conditional on SDK event content |
| Home Assistant | Emits request profile and `usage_unavailable` | Token totals unavailable |

## Implementation notes

- `StreamUsageRecorder` owns request timing, safe request profiling, recursive
  usage-object discovery, request-id capture, and exactly-once emission.
- Provider-specific adapters only feed SDK chunks or SSE events to the recorder.
- OpenAI Phase 1 code now uses the same recorder, avoiding two implementations
  of the logging contract.
- A valid provider-reported zero is preserved. Negative values such as `-1` are
  treated as unavailable.
- Turn totals remain strict: partial provider coverage does not masquerade as a
  complete total.

## Verification

The changed modules compile successfully, `git diff --check` passes, and the
containerized regression suite passes 19 tests. Coverage includes:

- OpenAI-compatible plain and function streams;
- Ollama final usage chunks;
- Gemini-style metadata normalization;
- nested SSE usage discovery;
- negative-count handling;
- missing-usage handling;
- exactly-once emission;
- existing OpenAI client configuration and Sherpa SenseVoice B2 tests.

## Deferred work

Phase 2 intentionally does not implement Token budgets, request blocking,
context pruning, tool-schema reduction, or model downgrade policies. The new
events provide the evidence required to design those controls without binding
them to one vendor.
