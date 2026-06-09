"""report_digest.py — 4개 분석 리포트를 토론/리스크용 압축 다이제스트로.

4개 분석가 리포트(market/sentiment/news/fundamentals)는 합산 ~12-15k 토큰으로,
하류 에이전트마다 통째로 임베드되어 파이프라인 입력 토큰의 대부분을 차지한다.
캐싱은 구조적으로 불가(각 에이전트의 서로 다른 f-string 중간에 위치)하므로,
대신 '한 번 압축해 여러 곳에서 재사용'한다.

설계:
- 토론(불/베어)·리스크(공격/중립/보수) 5개 *논증 생성* 에이전트는 다이제스트 사용.
- 결정권자(research_manager·trader·portfolio_manager)는 풀 리포트 유지(결정 품질 보호).
- 같은 4개 리포트에 대해 단 1회만 LLM 압축 → 모듈 메모(콘텐츠 해시 키)로 재사용.
  같은 실행(프로세스) 내 5개 에이전트가 1번의 압축 결과를 공유한다.
"""
from __future__ import annotations

import hashlib

_cache: dict[str, str] = {}

_PROMPT = (
    "You are condensing four equity-research analyst reports into a compact "
    "DIGEST for debate agents. Preserve EVERY concrete number, rating, signal, "
    "price level, catalyst, and risk. Drop verbose prose, methodology, hedging, "
    "and repetition. Use terse bullets under four headers: [Technical/Market], "
    "[Sentiment], [News/Macro], [Fundamentals]. Target ~450 words. Do not add "
    "opinions or a recommendation — only compress what the reports state.\n\n"
    "=== MARKET REPORT ===\n{market}\n\n=== SENTIMENT REPORT ===\n{sentiment}\n\n"
    "=== NEWS REPORT ===\n{news}\n\n=== FUNDAMENTALS REPORT ===\n{fundamentals}"
)


def get_reports_digest(market: str, sentiment: str, news: str,
                       fundamentals: str, llm) -> str:
    """4개 리포트의 압축 다이제스트를 반환(같은 입력은 1회만 LLM 호출, 이후 캐시).

    어떤 이유로든 압축이 실패하면 원본 4개를 이어붙인 문자열로 폴백(안전).
    """
    market = market or ""
    sentiment = sentiment or ""
    news = news or ""
    fundamentals = fundamentals or ""
    joined = "\x1f".join([market, sentiment, news, fundamentals])
    key = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    if key in _cache:
        return _cache[key]

    fallback = (f"Market research report: {market}\n\n"
                f"Social media sentiment report: {sentiment}\n\n"
                f"Latest world affairs news: {news}\n\n"
                f"Company fundamentals report: {fundamentals}")
    try:
        prompt = _PROMPT.format(market=market, sentiment=sentiment,
                                news=news, fundamentals=fundamentals)
        digest = llm.invoke(prompt).content
        digest = digest.strip() if isinstance(digest, str) else str(digest)
        result = ("[CONDENSED ANALYST DIGEST - key numbers/signals preserved; "
                  "full reports were summarized to save context]\n\n" + digest)
    except Exception:
        result = fallback

    _cache[key] = result
    return result
