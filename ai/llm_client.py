"""
ai/llm_client.py — Unified LangChain LLM wrapper supporting Gemini, OpenAI, and Claude.

Provides both synchronous and streaming completion so the UI can display tokens
as they arrive. Provider is selected from config.py and can be hot-swapped at
runtime via the Settings panel.
"""
import logging
import threading
from typing import Callable, Iterator, Optional

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _build_llm(provider: Optional[str] = None, streaming: bool = False):
    """
    Construct the appropriate LangChain chat model based on config.

    Args:
        provider: Override the configured provider ("gemini" | "openai" | "claude").
        streaming: If True, enable streaming mode.

    Returns:
        A LangChain BaseChatModel instance.
    """
    p = (provider or config.LLM_PROVIDER).lower().strip()

    if p == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI, HarmCategory, HarmBlockThreshold
        return ChatGoogleGenerativeAI(
            model=config.GEMINI_MODEL,
            google_api_key=config.GEMINI_API_KEY,
            streaming=streaming,
            temperature=0.3,
            max_output_tokens=1024,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )

    elif p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.OPENAI_MODEL,
            api_key=config.OPENAI_API_KEY,
            streaming=streaming,
            temperature=0.3,
            max_tokens=1024,
        )

    elif p in ("claude", "anthropic"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=config.ANTHROPIC_MODEL,
            api_key=config.ANTHROPIC_API_KEY,
            streaming=streaming,
            temperature=0.3,
            max_tokens=1024,
        )

    else:
        raise ValueError(
            f"Unknown LLM provider: {p!r}. "
            "Choose from: 'gemini', 'openai', 'claude'."
        )


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Thread-safe LLM client wrapper.

    Usage (synchronous):
        client = LLMClient()
        text = client.complete("You are a coach.", "What runes should I run?")

    Usage (streaming):
        for token in client.stream("You are a coach.", "What runes?"):
            print(token, end="", flush=True)

    Usage (async callback for UI):
        client.complete_async(system, user, on_token=update_ui, on_done=show_advice)
    """

    def __init__(self, provider: Optional[str] = None) -> None:
        self._provider = provider or config.LLM_PROVIDER
        self._lock = threading.Lock()
        logger.info("LLMClient initialised with provider: %s", self._provider)

    def set_provider(self, provider: str) -> None:
        """Hot-swap the LLM provider (called from Settings panel)."""
        with self._lock:
            self._provider = provider
        logger.info("LLM provider changed to: %s", provider)

    # -----------------------------------------------------------------------
    # Synchronous completion
    # -----------------------------------------------------------------------

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Blocking LLM call. Returns the full response string.
        Raises on API errors.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        with self._lock:
            provider = self._provider

        try:
            llm = _build_llm(provider=provider, streaming=False)
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            response = llm.invoke(messages)
            content = response.content
            logger.debug("LLM response (%d chars): %s…", len(content), content[:80])
            return content
        except Exception as exc:
            logger.error("LLM completion failed: %s", exc)
            raise

    # -----------------------------------------------------------------------
    # Streaming completion
    # -----------------------------------------------------------------------

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """
        Generator that yields tokens as they arrive from the LLM.
        Useful for building responsive streaming UIs.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        with self._lock:
            provider = self._provider

        llm = _build_llm(provider=provider, streaming=True)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        for chunk in llm.stream(messages):
            token = chunk.content
            if token:
                yield token

    # -----------------------------------------------------------------------
    # Async callback (for UI integration)
    # -----------------------------------------------------------------------

    def complete_async(
        self,
        system_prompt: str,
        user_prompt: str,
        on_token: Optional[Callable[[str], None]] = None,
        on_done: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> threading.Thread:
        """
        Run a streaming LLM call in a background thread.

        Args:
            on_token: Called for each token chunk as it arrives.
            on_done:  Called once with the full response when complete.
            on_error: Called with an error message string on failure.

        Returns:
            The daemon Thread (already started).
        """
        def _run():
            full_text = []
            try:
                for token in self.stream(system_prompt, user_prompt):
                    full_text.append(token)
                    if on_token:
                        on_token(token)
                if on_done:
                    on_done("".join(full_text))
            except Exception as exc:
                err_msg = f"LLM error ({self._provider}): {exc}"
                logger.error(err_msg)
                if on_error:
                    on_error(err_msg)

        t = threading.Thread(target=_run, daemon=True, name="LLM-Request")
        t.start()
        return t
