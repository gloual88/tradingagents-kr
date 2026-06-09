from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor

# LLM 컨텍스트에 실어 보낼 최근 행 수. 분석가는 '최근 추세 관찰 + get_indicators(전기간
# 독립 계산)'로 리포트를 쓰므로 수백 행 원본 전체는 불필요하다. 최근 N행 + 전기간 요약으로
# 압축해 토큰을 절약한다(예: 1년 256행 ≈ 8.9k토큰 → ~2.3k토큰). 지표는 get_indicators가
# 소스에서 따로 계산하므로 이 압축이 분석 정확도를 떨어뜨리지 않는다.
_RECENT_ROWS = 60


def _condense_ohlcv(raw: str, recent: int = _RECENT_ROWS) -> str:
    """get_stock_data 원본(주석헤더 + CSV)을 '전기간 요약 + 최근 N행'으로 압축.

    파싱에 실패하거나 이미 충분히 짧으면 원본을 그대로 반환한다(안전 우선).
    """
    try:
        lines = raw.split("\n")
        comments = [l.rstrip("\r") for l in lines if l.startswith("#")]
        hdr_idx = next(i for i, l in enumerate(lines)
                       if l.lower().startswith("date,"))
        header = lines[hdr_idx].rstrip("\r")
        rows = [l.rstrip("\r") for l in lines[hdr_idx + 1:] if l.strip()]
        if len(rows) <= recent:
            return raw

        def f(r, i):
            return float(r.split(",")[i])

        closes = [f(r, 4) for r in rows]
        highs = [f(r, 2) for r in rows]
        lows = [f(r, 3) for r in rows]
        vols = [f(r, 5) for r in rows]
        first_d = rows[0].split(",")[0]
        last_d = rows[-1].split(",")[0]
        chg = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0.0
        summary = (
            f"# Full-period summary ({first_d} -> {last_d}, "
            f"{len(rows)} trading days):\n"
            f"#   First close {closes[0]:.2f}, last close {closes[-1]:.2f} "
            f"({chg:+.1f}%)\n"
            f"#   Period high {max(highs):.2f}, low {min(lows):.2f}, "
            f"avg volume {sum(vols) / len(vols):,.0f}\n"
            f"# NOTE: only the most recent {recent} rows are shown below to save "
            f"context. Use get_indicators for technical indicators (SMA/EMA/MACD/"
            f"RSI/Bollinger/ATR/VWMA) computed over the ENTIRE period from source."
        )
        kept = rows[-recent:]
        return "\n".join(comments + ["", summary, "", header] + kept)
    except Exception:
        return raw


@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve stock price data (OHLCV) for a given ticker symbol.
    Uses the configured core_stock_apis vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A full-period summary plus the most recent rows of OHLCV data for
        the specified ticker and date range (condensed to save context; full
        technical indicators are available via get_indicators).
    """
    raw = route_to_vendor("get_stock_data", symbol, start_date, end_date)
    return _condense_ohlcv(raw)
