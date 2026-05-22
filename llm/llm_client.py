# llm/llm_client.py
# OpenAI LLM client.

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def get_llm() -> ChatOpenAI:
    """Return a ChatOpenAI client using the configured model.

    Returns:
        ChatOpenAI: Configured LLM client with temperature=0.0.
    """
    return ChatOpenAI(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        temperature=0.0,
        max_tokens=1024,
    )


if __name__ == "__main__":
    llm = get_llm()
    print(f"LLM ready: model={OPENAI_MODEL}")
