"""
뉴스 데이터 서비스
- 네이버 뉴스 (모바일 페이지 파싱)
- 다음 뉴스 (RSS)
- Google News RSS
- 한국 금융 매체 RSS (한경, 매경, 연합뉴스)
"""
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.parse import quote
import streamlit as st


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _parse_relative_time(pub_date_str: str) -> str:
    """RSS 날짜 → 상대 시간"""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_date_str)
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        diff = now - dt
        if diff < timedelta(minutes=1):
            return "방금"
        if diff < timedelta(hours=1):
            return f"{int(diff.seconds / 60)}분 전"
        if diff < timedelta(days=1):
            return f"{int(diff.seconds / 3600)}시간 전"
        if diff < timedelta(days=7):
            return f"{diff.days}일 전"
        return dt.strftime("%m/%d")
    except Exception:
        return ""


def _clean_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _fetch_rss(url: str, n: int = 15) -> list:
    """범용 RSS 파서"""
    try:
        req = Request(url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=10) as resp:
            data = resp.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    articles = []
    for item in root.findall(".//item"):
        if len(articles) >= n:
            break

        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = _clean_html(item.findtext("description") or "")
        source = (item.findtext("source") or "").strip()

        if not title:
            continue

        articles.append({
            "title": title,
            "link": link,
            "source": source,
            "published": pub,
            "time_ago": _parse_relative_time(pub) if pub else "",
            "snippet": desc[:200],
        })

    return articles


# ══════════════════════════════════════════════════
# 네이버 뉴스
# ══════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def fetch_naver_finance_news(n: int = 15) -> list:
    """
    네이버 금융 주요 뉴스 (모바일 JSON API 활용).
    """
    url = (
        "https://m.stock.naver.com/api/json/news/"
        "newsListJson.nhn?category=mainnews&page=1&pageSize="
        + str(n)
    )
    try:
        req = Request(url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        # 폴백: 네이버 증권 RSS
        return _fetch_naver_rss(n)

    articles = []
    items = data if isinstance(data, list) else data.get("result", [])
    if not isinstance(items, list):
        # API 응답이 예상과 다를 경우 폴백
        return _fetch_naver_rss(n)
    for item in items[:n]:
        title = item.get("tit", item.get("title", ""))
        link = item.get("url", item.get("link", ""))
        source = item.get("officeNm", item.get("officeName", ""))
        dt_str = item.get("dt", item.get("datetime", ""))

        if not title:
            continue

        # 네이버 링크 보정
        if link and not link.startswith("http"):
            link = "https://m.stock.naver.com" + link

        articles.append({
            "title": title,
            "link": link,
            "source": source or "네이버증권",
            "published": dt_str,
            "time_ago": _format_naver_time(dt_str),
            "snippet": "",
        })

    return articles


def _fetch_naver_rss(n: int = 15) -> list:
    """네이버 뉴스 경제 섹션 RSS (폴백)"""
    # 네이버 뉴스 경제 RSS
    # RSS가 아닌 HTML이므로 Google News로 폴백
    return fetch_google_news("site:news.naver.com 증시 코스피", n=n)


def _format_naver_time(dt_str: str) -> str:
    """네이버 날짜 포맷 → 상대 시간"""
    if not dt_str:
        return ""
    try:
        # "2026-03-10 09:30:00" 형식
        dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
        diff = datetime.now() - dt
        if diff < timedelta(minutes=1):
            return "방금"
        if diff < timedelta(hours=1):
            return f"{int(diff.seconds / 60)}분 전"
        if diff < timedelta(days=1):
            return f"{int(diff.seconds / 3600)}시간 전"
        if diff < timedelta(days=7):
            return f"{diff.days}일 전"
        return dt.strftime("%m/%d")
    except Exception:
        return dt_str[:10] if len(dt_str) >= 10 else ""


@st.cache_data(ttl=600, show_spinner=False)
def fetch_naver_search_news(query: str, n: int = 10) -> list:
    """
    네이버 뉴스 검색 (모바일 검색 페이지 파싱).
    """
    encoded = quote(query)
    url = (
        f"https://m.search.naver.com/search.naver?"
        f"where=m_news&query={encoded}&sm=mtb_nmr"
    )
    try:
        req = Request(url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return fetch_google_news(query, n=n)

    articles = []
    # 간단한 정규식으로 뉴스 제목/링크 추출
    pattern = re.compile(
        r'<a[^>]*class="news_tit"[^>]*href="([^"]+)"[^>]*'
        r'title="([^"]+)"',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        if len(articles) >= n:
            break
        link, title = match.group(1), match.group(2)
        articles.append({
            "title": _clean_html(title),
            "link": link,
            "source": "네이버뉴스",
            "published": "",
            "time_ago": "",
            "snippet": "",
        })

    # 출처/시간 추출 시도
    info_pattern = re.compile(
        r'<span class="info_group">[^<]*<a[^>]*>([^<]+)</a>'
        r'[^<]*<span[^>]*>([^<]+)</span>',
        re.DOTALL,
    )
    for i, match in enumerate(info_pattern.finditer(html)):
        if i >= len(articles):
            break
        articles[i]["source"] = _clean_html(match.group(1))
        articles[i]["time_ago"] = _clean_html(match.group(2))

    return articles if articles else fetch_google_news(query, n=n)


# ══════════════════════════════════════════════════
# 다음 뉴스
# ══════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def fetch_daum_finance_news(n: int = 15) -> list:
    """다음 금융 뉴스 (RSS)"""
    # 다음 뉴스 경제 RSS
    url = "https://news.daum.net/rss/economy"
    articles = _fetch_rss(url, n=n)
    for a in articles:
        if not a["source"]:
            a["source"] = "다음뉴스"
    return articles


@st.cache_data(ttl=600, show_spinner=False)
def fetch_daum_stock_news(n: int = 15) -> list:
    """다음 증권 뉴스"""
    url = "https://news.daum.net/rss/stock"
    articles = _fetch_rss(url, n=n)
    if not articles:
        # 폴백: 경제 RSS
        articles = fetch_daum_finance_news(n=n)
    for a in articles:
        if not a["source"]:
            a["source"] = "다음증권"
    return articles


# ══════════════════════════════════════════════════
# Google News (기존)
# ══════════════════════════════════════════════════

@st.cache_data(ttl=900, show_spinner=False)
def fetch_google_news(query: str, n: int = 10) -> list:
    encoded = quote(query)
    url = (
        f"https://news.google.com/rss/search?"
        f"q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    )
    return _fetch_rss(url, n=n)


# ══════════════════════════════════════════════════
# 한국 금융 매체 RSS
# ══════════════════════════════════════════════════

_KR_FINANCE_RSS = {
    "한국경제": "https://www.hankyung.com/feed/stock",
    "매일경제": "https://www.mk.co.kr/rss/30100041/",
    "연합뉴스 경제": "https://www.yna.co.kr/rss/economy.xml",
    "이데일리": "https://rss.edaily.co.kr/edaily_stock.xml",
    "Investing.com": "https://kr.investing.com/rss/news.rss",
}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_kr_media_news(source_name: str = None, n: int = 10) -> list:
    """
    한국 금융 매체 RSS 뉴스.
    source_name=None 이면 전체 매체에서 수집.
    """
    if source_name and source_name in _KR_FINANCE_RSS:
        articles = _fetch_rss(_KR_FINANCE_RSS[source_name], n=n)
        for a in articles:
            if not a["source"]:
                a["source"] = source_name
        return articles

    # 전체 매체
    all_articles = []
    for name, url in _KR_FINANCE_RSS.items():
        items = _fetch_rss(url, n=5)
        for a in items:
            if not a["source"]:
                a["source"] = name
        all_articles.extend(items)

    # 시간순 정렬 (최신 먼저)
    all_articles.sort(
        key=lambda x: x.get("published", ""),
        reverse=True,
    )
    return all_articles[:n]


# ══════════════════════════════════════════════════
# X (Twitter) — 금융 계정 via Google News 우회
# ══════════════════════════════════════════════════

# 주요 금융 X 계정 (한국+글로벌)
_X_FINANCE_ACCOUNTS = [
    "zaborhedgefund", "MacroAlf", "WallStreetSilv",
    "KoreaIR", "haboroid", "elaborateyj",
]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_x_finance_news(n: int = 10) -> list:
    """X(Twitter) 금융 계정 피드 — Google News 우회 검색."""
    # 방법 1: Nitter RSS (무료 대안)
    nitter_instances = [
        "nitter.privacydev.net",
        "nitter.poast.org",
    ]
    all_articles = []
    for account in _X_FINANCE_ACCOUNTS[:3]:
        for host in nitter_instances:
            url = f"https://{host}/{account}/rss"
            items = _fetch_rss(url, n=3)
            if items:
                for a in items:
                    a["source"] = f"X @{account}"
                all_articles.extend(items)
                break  # 작동하는 인스턴스 찾으면 다음 계정으로

    # 방법 2: Nitter 실패 시 Google News 우회 (영문)
    if len(all_articles) < 3:
        query = "site:x.com OR site:twitter.com stock market finance"
        google_items = _fetch_rss(
            f"https://news.google.com/rss/search?q={quote(query)}&hl=en&gl=US&ceid=US:en",
            n=n,
        )
        for a in google_items:
            a["source"] = "X (via Google)"
        all_articles.extend(google_items)

    return all_articles[:n]


# ══════════════════════════════════════════════════
# Investing.com 한국어
# ══════════════════════════════════════════════════

_INVESTING_RSS = {
    "뉴스": "https://kr.investing.com/rss/news.rss",
    "분석": "https://kr.investing.com/rss/news_14.rss",
    "주식": "https://kr.investing.com/rss/news_25.rss",
    "경제지표": "https://kr.investing.com/rss/news_1.rss",
}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_investing_news(category: str = None, n: int = 10) -> list:
    """Investing.com 한국어 뉴스 (RSS)."""
    if category and category in _INVESTING_RSS:
        articles = _fetch_rss(_INVESTING_RSS[category], n=n)
        for a in articles:
            if not a["source"]:
                a["source"] = f"Investing.com {category}"
        return articles

    # 전체 카테고리 통합
    all_articles = []
    for name, url in _INVESTING_RSS.items():
        items = _fetch_rss(url, n=4)
        for a in items:
            if not a["source"]:
                a["source"] = "Investing.com"
        all_articles.extend(items)

    all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)
    return all_articles[:n]


# ══════════════════════════════════════════════════
# 통합 API (컴포넌트에서 호출)
# ══════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def fetch_market_news(n: int = 20) -> list:
    """시장 뉴스 (다음 + 네이버 + Investing.com + X + Google 통합)"""
    all_news = []

    # 다음 증권
    all_news.extend(fetch_daum_stock_news(n=6))
    # 네이버 금융
    all_news.extend(fetch_naver_finance_news(n=6))
    # Investing.com 한국어
    all_news.extend(fetch_investing_news(n=5))
    # X (Twitter) 금융
    all_news.extend(fetch_x_finance_news(n=4))

    # 중복 제거 (제목 기준)
    seen = set()
    unique = []
    for a in all_news:
        key = a["title"][:30]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # 부족하면 Google News 보충
    if len(unique) < n:
        google = fetch_google_news("코스피 증시", n=n - len(unique))
        for a in google:
            key = a["title"][:30]
            if key not in seen:
                seen.add(key)
                unique.append(a)

    return unique[:n]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_etf_news(n: int = 10) -> list:
    """ETF/연금 뉴스"""
    return fetch_google_news("ETF 퇴직연금 자산배분", n=n)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_ticker_news(name: str, n: int = 5) -> list:
    """종목별 뉴스 (네이버 검색 우선)"""
    articles = fetch_naver_search_news(name, n=n)
    if not articles:
        articles = fetch_google_news(name, n=n)
    return articles
