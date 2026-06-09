import os
from typing import Any, Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

load_dotenv()

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "max_tokens",
    "callbacks", "http_client", "http_async_client", "effort",
)

# 출력 언어 지시 — TRADINGAGENTS_OUTPUT_LANGUAGE 가 English 가 아니면 모든 호출에 주입.
# 등급 키워드·티커·숫자·신호 문구는 영어로 유지(process_signal 추출 + 신호 파싱 보호).
_LANG_DIRECTIVE = (
    "\n\n[LANGUAGE INSTRUCTION] Write your ENTIRE response in {lang}. "
    "Keep the following in English exactly: ticker symbols, numbers, dates, "
    "the rating keywords (BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL), and the "
    "phrase 'FINAL TRANSACTION PROPOSAL'."
)


def _target_language() -> Optional[str]:
    lang = (os.environ.get("TRADINGAGENTS_OUTPUT_LANGUAGE") or "").strip()
    if lang and lang.lower() not in ("english", "en"):
        return lang
    return None


def _append_text(content, text):
    if isinstance(content, str):
        return content + text
    if isinstance(content, list):
        return list(content) + [{"type": "text", "text": text}]
    return content


def _inject_language(input, directive):
    """프롬프트(문자열/PromptValue/메시지리스트)에 언어 지시를 덧붙인다.
    시스템 메시지가 있으면 거기에(툴루프 내내 유지), 없으면 마지막 메시지에."""
    if isinstance(input, str):
        return input + directive
    if hasattr(input, "to_messages"):
        msgs = list(input.to_messages())
    elif isinstance(input, list) and input and isinstance(input[0], BaseMessage):
        msgs = list(input)
    else:
        return input
    idx = next((i for i, m in enumerate(msgs)
                if getattr(m, "type", None) == "system"), len(msgs) - 1)
    m = msgs[idx]
    msgs[idx] = m.model_copy(update={"content": _append_text(m.content, directive)})
    return msgs


class NormalizedChatAnthropic(ChatAnthropic):
    """ChatAnthropic with normalized content output + optional output-language.

    Claude models with extended thinking or tool use return content as a
    list of typed blocks. This normalizes to string for consistent
    downstream handling. If TRADINGAGENTS_OUTPUT_LANGUAGE is set (non-English),
    a language directive is injected into every call so all agents (analysts,
    debate, risk, managers) respond in that language.
    """

    def invoke(self, input, config=None, **kwargs):
        try:
            lang = _target_language()
            if lang:
                input = _inject_language(input, _LANG_DIRECTIVE.format(lang=lang))
        except Exception:
            pass  # 언어 주입은 베스트에포트 — 실패해도 호출은 정상 진행
        return normalize_content(super().invoke(input, config, **kwargs))


class AnthropicClient(BaseLLMClient):
    """Client for Anthropic Claude models."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatAnthropic instance."""
        llm_kwargs = {"model": self.model}

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        if "api_key" not in llm_kwargs:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                llm_kwargs["api_key"] = api_key

        return NormalizedChatAnthropic(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Anthropic."""
        return validate_model("anthropic", self.model)
