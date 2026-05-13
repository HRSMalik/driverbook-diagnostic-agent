# llm/parsers.py — Extract and normalize the first JSON object from an LLM response.

import json
import re
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage


def invoke_and_parse(llm: ChatOllama, messages: list[BaseMessage]) -> dict[str, Any]:
    """Invoke the LLM and extract the first JSON object from its response.

    Args:
        llm:      Configured ChatOllama instance.
        messages: List of SystemMessage / HumanMessage to send.

    Returns:
        Parsed dict on success, or {"error": ..., "raw": ...} / {"error": ...} on failure.
    """
    try:
        response = llm.invoke(messages)
        raw_text = response.content if hasattr(response, "content") else str(response)
        clean = re.sub(r"\s+", " ", raw_text).strip()
        match = re.search(r"\{.*\}", clean)
        return json.loads(match.group(0)) if match else {"error": "No JSON found", "raw": clean}
    except Exception as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    from langchain_core.messages import SystemMessage, HumanMessage
    from llm.hf_client import get_llm

    _llm = get_llm()
    _msgs = [
        SystemMessage(content="You are a test agent. Output raw single-line JSON only."),
        HumanMessage(content='Return {"ok": true}'),
    ]
    print(invoke_and_parse(_llm, _msgs))
