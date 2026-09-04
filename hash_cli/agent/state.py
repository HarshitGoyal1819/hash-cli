"""Agent state definition for the LangGraph ReAct loop."""

from __future__ import annotations

from typing import Annotated, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """The mutable state that flows through LangGraph nodes.

    messages: Full conversation history. LangGraph's add_messages reducer
              appends new messages rather than replacing the list, so each
              node only needs to return the *new* messages it produced.
    """

    messages: Annotated[Sequence[BaseMessage], add_messages]
