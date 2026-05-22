# llm/llm_client.py
# OpenAI LLM client.

from langchain_openai import ChatOpenAI

from config.settings import settings


def get_llm() -> ChatOpenAI:
    """Return a ChatOpenAI client using the configured model.

    Returns:
        ChatOpenAI: Configured LLM client with temperature=0.0.
    """
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.0,
        max_tokens=1024,
    )


if __name__ == "__main__":
    llm = get_llm()
    print(f"LLM ready: model={settings.OPENAI_MODEL}")
