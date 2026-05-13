# llm/hf_client.py
# Ollama LLM client.

import os
from dotenv import load_dotenv
from langchain_ollama import ChatOllama

load_dotenv()

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


def get_llm() -> ChatOllama:
    """Return a ChatOllama client using the configured local model.

    Returns:
        ChatOllama: Configured LLM client with temperature=0.0 and 512 token cap.
    """
    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.0,
        num_predict=4096,
    )


if __name__ == "__main__":
    llm = get_llm()
    print(f"LLM ready: model={OLLAMA_MODEL} base_url={OLLAMA_BASE_URL}")
