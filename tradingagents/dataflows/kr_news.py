"""Korean news data fetcher for TradingAgents.

Bridges kr_dashboard/services/news_data.py into the TradingAgents
dataflows layer so KOSPI/KOSDAQ tickers (.KS / .KQ suffix) receive
Korean-localized news for the News Analyst and Sentiment Analyst.

Sources mixed:
  - 네이버 종목 검색 (fetch_naver_search_news)
  - 다음 주식 뉴스 (fetch_daum_stock_news)
  - Google News 한국어 (fetch_google_news)
  - X(Twitter) 금융 — retail sentiment proxy (fetch_x_finance_news)
  - 한경/매경/연합 RSS (fetch_kr_media_news)
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path


# Locate kr_dashboard root: this file lives at
#   d:\파이선\kr_dashboard\tradingagents\tradingagents\dataflows\kr_news.py
# parents[3] → d:\파이선\kr_dashboard
_KR_DASH_ROOT = Path(__file__).resolve().parents[3]
if str(_KR_DASH_ROOT) not in sys.path:
    sys.path.insert(0, str(_KR_DASH_ROOT))

# Streamlit cache_data decorators in news_data.py warn when called
# outside a Streamlit script — silence those warnings.
warnings.filterwarnings(
    "ignore",
    message=".*cache_data.*",
)
warnings.filterwarnings(
    "ignore",
    message=".*ScriptRunContext.*",
)


_KR_INDEX_NAMES = {
    "^KS11":  "코스피 종합지수",
    "^KS200": "코스피 200",
    "^KQ11":  "코스닥 종합지수",
    "^KQ150": "코스닥 150",
}


def _is_kr_ticker(ticker: str) -> bool:
    """`.KS` / `.KQ` 종목 또는 `^KS*` / `^KQ*` 한국 지수."""
    return (
        ticker.endswith((".KS", ".KQ"))
        or ticker in _KR_INDEX_NAMES
        or ticker.startswith(("^KS", "^KQ"))
    )


def _ticker_to_kr_name(ticker: str) -> str:
    """005930.KS → 삼성전자, 069500.KS → KODEX 200, ^KS11 → 코스피 종합지수.

    Resolution order:
      1. 한국 지수 lookup
      2. pykrx 상장주식 (가장 빠름)
      3. yfinance longName (ETF / 누락 종목 폴백)
      4. 티커 원본
    """
    if ticker in _KR_INDEX_NAMES:
        return _KR_INDEX_NAMES[ticker]
    if not ticker.endswith((".KS", ".KQ")):
        return ticker
    code = ticker.split(".")[0]

    try:
        from pykrx import stock
        name = stock.get_market_ticker_name(code)
        if name and not isinstance(name, str) is False and name != code:
            if isinstance(name, str) and name.strip():
                return name.strip()
    except Exception:
        pass

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        for key in ("longName", "shortName"):
            val = info.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
    except Exception:
        pass

    return ticker


def _parse_article_date(art: dict) -> datetime | None:
    """Best-effort date parse from a fetched article dict."""
    pub_str = art.get("published", "")
    if not pub_str:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(pub_str[: len(fmt)], fmt)
        except ValueError:
            continue
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(pub_str).replace(tzinfo=None)
    except Exception:
        return None


def _dedupe(articles: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for a in articles:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        key = title[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def get_news_kr(ticker: str, start_date: str, end_date: str) -> str:
    """KR ticker news → formatted markdown for the analyst LLM.

    Returns the same shape as get_news_yfinance so the agent prompt
    doesn't care about the source.
    """
    name = _ticker_to_kr_name(ticker)

    try:
        try:  # vendored copy (배포 시 패키지에 포함 → 외부 PC에서도 작동)
            from .kr_news_source import (
                fetch_naver_search_news,
                fetch_google_news,
                fetch_daum_stock_news,
                fetch_x_finance_news,
            )
        except ImportError:  # 폴백: kr_dashboard/services (원 개발 환경)
            from services.news_data import (
                fetch_naver_search_news,
                fetch_google_news,
                fetch_daum_stock_news,
                fetch_x_finance_news,
            )
    except ImportError as e:
        return (
            f"## KR news fetch failed for {ticker} ({name})\n\n"
            f"kr_dashboard 서비스 임포트 실패: {e}"
        )

    pool: list[dict] = []
    try:
        pool.extend(fetch_naver_search_news(name, n=15))
    except Exception:
        pass
    try:
        pool.extend(fetch_naver_search_news(f"{name} 주가", n=10))
    except Exception:
        pass
    try:
        pool.extend(fetch_google_news(f"{name} 주식 OR 실적 OR 전망", n=10))
    except Exception:
        pass
    try:
        pool.extend(fetch_daum_stock_news(n=10))
    except Exception:
        pass
    try:
        pool.extend(fetch_x_finance_news(n=5))
    except Exception:
        pass

    pool = _dedupe(pool)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    filtered: list[dict] = []
    for art in pool:
        pub_dt = _parse_article_date(art)
        if pub_dt is None:
            filtered.append(art)
            continue
        if start_dt <= pub_dt <= end_dt:
            filtered.append(art)

    if not filtered:
        return (
            f"No KR news found for {ticker} ({name}) "
            f"between {start_date} and {end_date}"
        )

    body = "".join(_format_article(a) for a in filtered[:25])
    return (
        f"## {ticker} ({name}) KR News, from {start_date} to {end_date}:\n\n"
        f"_데이터 소스: 네이버 검색 + 다음 주식 + Google News 한국어 + X 금융_\n\n"
        f"{body}"
    )


def get_global_news_kr(
    curr_date: str,
    look_back_days: int = 7,
    limit: int = 10,
) -> str:
    """KR-focused macro/global news (한국은행, 코스피, 환율, 한미 무역 등)."""
    try:
        try:
            from .kr_news_source import fetch_google_news, fetch_kr_media_news
        except ImportError:
            from services.news_data import fetch_google_news, fetch_kr_media_news
    except ImportError as e:
        return f"## KR global news fetch failed\n\n{e}"

    queries = [
        "한국은행 기준금리 물가",
        "코스피 코스닥 경제전망",
        "원달러 환율 미국 금리",
        "한국 수출 무역수지 GDP",
        "미중 무역 한국 영향",
    ]

    pool: list[dict] = []
    for q in queries:
        try:
            pool.extend(fetch_google_news(q, n=5))
        except Exception:
            continue
    try:
        pool.extend(fetch_kr_media_news(n=10))
    except Exception:
        pass

    pool = _dedupe(pool)

    if not pool:
        return f"## KR Macro News (as of {curr_date}) — no articles\n"

    body = "".join(_format_article(a) for a in pool[:limit])
    return (
        f"## KR Macro News (as of {curr_date}, "
        f"last {look_back_days} days):\n\n{body}"
    )


def _format_article(art: dict) -> str:
    title = art.get("title", "").strip()
    source = art.get("source", "").strip()
    time_ago = art.get("time_ago", "").strip()
    snippet = (art.get("snippet") or "").strip()
    link = art.get("link", "").strip()

    meta_parts = [p for p in (source, time_ago) if p]
    meta = f"source: {', '.join(meta_parts)}" if meta_parts else "source: 미상"

    out = f"### {title} ({meta})\n"
    if snippet:
        out += f"{snippet}\n"
    if link:
        out += f"Link: {link}\n"
    out += "\n"
    return out
