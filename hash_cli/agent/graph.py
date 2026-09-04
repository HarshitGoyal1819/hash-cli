"""LangGraph ReAct agent graph definition."""

from __future__ import annotations

import json
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Iterator, Literal

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from hash_cli.agent.prompts import build_system_prompt
from hash_cli.agent.state import AgentState
from hash_cli.tools import ALL_TOOLS


@dataclass
class AgentConfig:
    """Runtime configuration for the agent."""

    model: str = "llama3.1:8b"
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    openai_base_url: str = ""          # for OpenAI-compatible APIs like DeepSeek
    temperature: float = 0.0
    cwd: str | None = None
    extra_tools: list = field(default_factory=list)

    @classmethod
    def from_active_config(cls, cwd: str | None = None) -> "AgentConfig":
        from hash_cli.config import get_active_model_info
        info = get_active_model_info()
        return cls(
            model=info["model"],
            provider=info["provider"],
            openai_base_url=info.get("base_url", ""),
            cwd=cwd,
        )


# ---------------------------------------------------------------------------
# Tool-call JSON fallback parser
#
# Some Ollama models (e.g. qwen2.5-coder) output tool calls as raw JSON text
# instead of using the structured tool_calls field.  This parser detects that
# pattern and promotes the text into a proper AIMessage.tool_calls list so
# the ToolNode can execute them normally.
# ---------------------------------------------------------------------------

# Patterns the model might use for raw JSON tool calls
_TC_PATTERNS = [
    # {"name": "write_file", "arguments": {...}}
    re.compile(r'\{[^{}]*"name"\s*:\s*"(\w+)"[^{}]*"arguments"\s*:\s*(\{.*?\})\s*\}', re.DOTALL),
    # {"tool": "write_file", "tool_input": {...}}
    re.compile(r'\{[^{}]*"tool"\s*:\s*"(\w+)"[^{}]*"tool_input"\s*:\s*(\{.*?\})\s*\}', re.DOTALL),
]


def _extract_raw_tool_calls(text: str) -> list[dict] | None:
    """Try to parse tool call JSON embedded in plain text.

    Returns a list of {name, args} dicts if found, else None.
    """
    calls = []
    for pattern in _TC_PATTERNS:
        for match in pattern.finditer(text):
            try:
                name = match.group(1)
                args = json.loads(match.group(2))
                calls.append({"name": name, "args": args, "id": str(uuid.uuid4())[:8]})
            except (json.JSONDecodeError, IndexError):
                continue
    return calls if calls else None


def _coerce_tool_calls(response: AIMessage) -> AIMessage:
    """If the model emitted raw JSON instead of structured tool_calls, fix it."""
    if response.tool_calls:
        return response  # already correct

    content = response.content if isinstance(response.content, str) else ""
    if not content.strip():
        return response

    raw_calls = _extract_raw_tool_calls(content)
    if not raw_calls:
        return response

    # Rebuild as a proper AIMessage with tool_calls
    tool_calls = [
        {"name": c["name"], "args": c["args"], "id": f"call_{c['id']}", "type": "tool_call"}
        for c in raw_calls
    ]
    return AIMessage(content="", tool_calls=tool_calls)


# ---------------------------------------------------------------------------
# Conversational guard — prevent small models from calling tools on greetings
# ---------------------------------------------------------------------------

_CONVERSATIONAL_RE = re.compile(
    r"^\s*(hi+|hey+|hello+|howdy|yo+|sup|hiya|"
    r"how are you|how's it going|how do you do|"
    r"good morning|good afternoon|good evening|good night|"
    r"thanks|thank you|thx|ty|cheers|"
    r"ok|okay|alright|sure|got it|sounds good|great|nice|cool|"
    r"bye|goodbye|see you|cya|later)\W*$",
    re.IGNORECASE,
)

_WEB_SEARCH_TOOLS = {"web_search", "web_fetch"}


def _guard_tool_calls(response: AIMessage, user_text: str) -> AIMessage:
    """Strip tool calls that are inappropriate for the given user message.

    Small models often call web_search on casual greetings. This guard
    removes those calls so the agent just replies conversationally.
    """
    if not response.tool_calls:
        return response

    user_stripped = user_text.strip()

    # Block web search on short conversational messages
    is_casual = bool(_CONVERSATIONAL_RE.match(user_stripped)) or len(user_stripped) < 20

    if is_casual:
        # Remove web_search / web_fetch calls — keep any legitimate file/shell tools
        filtered = [tc for tc in response.tool_calls
                    if tc.get("name", "") not in _WEB_SEARCH_TOOLS]
        if len(filtered) < len(response.tool_calls):
            return AIMessage(
                content=response.content,
                tool_calls=filtered,
            )

    return response


# ---------------------------------------------------------------------------
# Node definitions
# ---------------------------------------------------------------------------

def _make_llm_node(llm_with_tools, system_prompt: str):
    def llm_node(state: AgentState) -> dict:
        messages = list(state["messages"])
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + messages

        # Extract the latest user message for the guard check
        from langchain_core.messages import HumanMessage
        user_text = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_text = msg.content if isinstance(msg.content, str) else ""
                break

        response = llm_with_tools.invoke(messages)
        response = _coerce_tool_calls(response)
        response = _guard_tool_calls(response, user_text)
        return {"messages": [response]}
    return llm_node


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "__end__"


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _build_llm(config: AgentConfig):
    """Return the appropriate LangChain chat model for the given provider."""
    if config.provider == "ollama":
        return ChatOllama(
            model=config.model,
            base_url=config.base_url,
            temperature=config.temperature,
        )
    elif config.provider == "openai":
        from langchain_openai import ChatOpenAI
        kwargs: dict = {"model": config.model, "temperature": config.temperature}

        if config.openai_base_url:
            # OpenAI-compatible API (e.g. DeepSeek)
            kwargs["base_url"] = config.openai_base_url
            import os
            dk = os.environ.get("DEEPSEEK_API_KEY")
            if dk:
                kwargs["api_key"] = dk
        else:
            # GPT-5.x family requires reasoning_effort='none' to use function tools
            # via chat completions. Applies to gpt-5, gpt-5.6-luna/terra/sol, etc.
            if config.model.startswith("gpt-5"):
                # temperature is also not adjustable on some gpt-5 models — drop it
                kwargs.pop("temperature", None)
                kwargs["reasoning_effort"] = "none"

        return ChatOpenAI(**kwargs)
    elif config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=config.model, temperature=config.temperature)
    elif config.provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.model, temperature=config.temperature)
    else:
        raise ValueError(f"Unknown provider: {config.provider}")


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def create_agent(config: AgentConfig | None = None):
    """Build and compile the LangGraph ReAct agent."""
    if config is None:
        config = AgentConfig()

    tools = ALL_TOOLS + config.extra_tools
    system_prompt = build_system_prompt(cwd=config.cwd)

    llm = _build_llm(config)
    llm_with_tools = llm.bind_tools(tools)

    tool_node = ToolNode(tools)
    llm_node = _make_llm_node(llm_with_tools, system_prompt)

    graph = StateGraph(AgentState)
    graph.add_node("agent", llm_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile()


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class UsageStats:
    """Token usage for one agent turn."""
    input_tokens:  int = 0
    output_tokens: int = 0
    total_tokens:  int = 0

    def add(self, other: "UsageStats") -> None:
        self.input_tokens  += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens  += other.total_tokens

    @classmethod
    def from_metadata(cls, meta: dict | None) -> "UsageStats":
        if not meta:
            return cls()
        return cls(
            input_tokens  = meta.get("input_tokens",  meta.get("prompt_tokens", 0)),
            output_tokens = meta.get("output_tokens", meta.get("completion_tokens", 0)),
            total_tokens  = meta.get("total_tokens",  0),
        )

    def is_empty(self) -> bool:
        return self.total_tokens == 0 and self.input_tokens == 0


@dataclass
class StreamEvent:
    """A single event emitted during a streamed agent run."""

    kind: Literal["token", "tool_start", "tool_end", "error", "done", "usage"]
    content:    str = ""
    tool_name:  str = ""
    tool_input: dict = field(default_factory=dict)
    tool_output: str = ""
    usage: "UsageStats | None" = None


# ---------------------------------------------------------------------------
# Real-time streaming via thread + queue
# (fixes spinner: graph runs in background, events arrive as they happen)
# ---------------------------------------------------------------------------

_SENTINEL = object()  # signals end of stream


def stream_agent_realtime(
    graph,
    user_message: str,
    history: list[BaseMessage],
) -> tuple[Iterator[StreamEvent], threading.Event]:
    """Run the agent in a background thread, yielding events in real time.

    Returns (event_iterator, history_ready_event).
    After the iterator is exhausted, call get_updated_history() to get
    the new history list.

    This is the key fix for the spinner: the graph.stream() blocking call
    runs on a separate thread so the main thread can animate the spinner
    while waiting for events.
    """
    from langchain_core.messages import HumanMessage

    q: queue.Queue = queue.Queue()
    human_msg = HumanMessage(content=user_message)
    messages = list(history) + [human_msg]
    state: AgentState = {"messages": messages}

    all_new_messages: list[BaseMessage] = [human_msg]
    result_holder: list[list[BaseMessage]] = []

    def _worker():
        turn_usage = UsageStats()
        try:
            for chunk in graph.stream(state, stream_mode="updates"):
                for _node, node_output in chunk.items():
                    for msg in node_output.get("messages", []):
                        all_new_messages.append(msg)
                        if isinstance(msg, AIMessage):
                            # Accumulate token usage from every LLM call in this turn
                            msg_usage = UsageStats.from_metadata(
                                getattr(msg, "usage_metadata", None)
                            )
                            if not msg_usage.is_empty():
                                turn_usage.add(msg_usage)

                            for tc in msg.tool_calls or []:
                                q.put(StreamEvent(
                                    kind="tool_start",
                                    tool_name=tc["name"],
                                    tool_input=tc.get("args", {}),
                                ))
                            if msg.content:
                                text = msg.content if isinstance(msg.content, str) else str(msg.content)
                                q.put(StreamEvent(kind="token", content=text))
                        elif isinstance(msg, ToolMessage):
                            q.put(StreamEvent(
                                kind="tool_end",
                                tool_name=msg.name or "",
                                tool_output=str(msg.content)[:500],
                            ))

            # Emit usage summary before done
            if not turn_usage.is_empty():
                # Ensure total is always set
                if turn_usage.total_tokens == 0:
                    turn_usage.total_tokens = turn_usage.input_tokens + turn_usage.output_tokens
                q.put(StreamEvent(kind="usage", usage=turn_usage))

            q.put(StreamEvent(kind="done"))
        except Exception as exc:
            q.put(StreamEvent(kind="error", content=str(exc)))
        finally:
            result_holder.append(list(history) + all_new_messages)
            q.put(_SENTINEL)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    # Wrap in a class so we can attach get_history() as an attribute.
    # Plain generators don't support attribute assignment.
    class _EventStream:
        def __init__(self):
            self._thread = thread

        def __iter__(self):
            while True:
                item = q.get()
                if item is _SENTINEL:
                    break
                yield item

        def get_history(self) -> list[BaseMessage]:
            # Wait for worker to finish then return history
            self._thread.join(timeout=60)
            if result_holder:
                return result_holder[0]
            return list(history) + all_new_messages

    return _EventStream()


# ---------------------------------------------------------------------------
# Sync runner (used for --no-stream mode)
# ---------------------------------------------------------------------------

def run_agent(
    graph,
    user_message: str,
    history: list[BaseMessage],
) -> tuple[str, list[BaseMessage]]:
    """Run the agent synchronously and return (final_text, updated_history)."""
    from langchain_core.messages import HumanMessage

    human_msg = HumanMessage(content=user_message)
    messages = list(history) + [human_msg]
    state: AgentState = {"messages": messages}

    result = graph.invoke(state)
    all_messages: list[BaseMessage] = list(result["messages"])

    final_text = ""
    for msg in reversed(all_messages):
        if isinstance(msg, AIMessage) and msg.content:
            final_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    new_history = all_messages[len(history):]
    return final_text, list(history) + new_history
