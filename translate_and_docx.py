"""Translate TradingAgents English reports to Korean and save as .docx.

Usage:
    python translate_and_docx.py SPY 2026-05-15
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from md_to_docx import REPORT_ORDER, md_to_docx


TRANSLATE_SYSTEM = (
    "당신은 한국 금융업계의 전문 번역가입니다. "
    "영어로 작성된 미국 주식 분석 리포트를 한국어로 번역합니다."
)

TRANSLATE_PROMPT = """다음 영어 트레이딩 분석 리포트를 한국어로 번역하세요.

규칙:
1. 마크다운 구조 완전 보존:
   - 헤딩 (#, ##, ###, ####)
   - 리스트 (-, *, 1.)
   - 표 (|)
   - 코드 블록 (```)
   - 인라인 강조 (**굵게**, *기울임*, `코드`)
2. 그대로 유지:
   - 종목 코드 (SPY, NVDA, AAPL 등)
   - 숫자, 가격($748.17), 퍼센트(18%)
   - 기술 지표 약어 (MACD, RSI, SMA, EMA, ATR, ADX, Bollinger 등)
   - 영문 약어 (P/E, PPI, CPI, GDP, Fed, ECB 등)
3. 자연스러운 한국 증권사 리포트 문체로 번역
4. 번역 결과만 출력 — "다음은 번역입니다" 같은 머리말 절대 금지

원문:
---
{text}
---"""


def translate(client: Anthropic, model: str, md_text: str, label: str) -> str:
    print(f"  [{label}] 번역 중 ({len(md_text):,} chars)...", flush=True)
    t0 = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=TRANSLATE_SYSTEM,
        messages=[{
            "role": "user",
            "content": TRANSLATE_PROMPT.format(text=md_text),
        }],
    )
    out = response.content[0].text.strip()
    usage = response.usage
    dt = time.time() - t0
    print(
        f"    완료 ({dt:.1f}s, "
        f"in={usage.input_tokens} out={usage.output_tokens})",
        flush=True,
    )
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--logs-root",
                        default=os.path.expanduser("~/.tradingagents/logs"))
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY가 환경변수/.env에 없습니다")

    run_dir = Path(args.logs_root) / args.ticker / args.date
    reports_dir = run_dir / "reports"
    if not reports_dir.exists():
        sys.exit(f"리포트 폴더가 없습니다: {reports_dir}")

    client = Anthropic(api_key=api_key)

    print(f"=== {args.ticker} {args.date} 한국어 번역 시작 (model={args.model}) ===")
    parts = []
    for fname, section_title in REPORT_ORDER:
        f = reports_dir / fname
        if not f.exists():
            print(f"  [스킵] {fname} 없음")
            continue
        body = f.read_text(encoding="utf-8").strip()
        translated = translate(client, args.model, body, section_title)
        parts.append(f"# {section_title}\n\n{translated}")

    md_text = "\n\n---\n\n".join(parts)

    reports_out = Path(__file__).parent / "reports"
    reports_out.mkdir(parents=True, exist_ok=True)

    translated_md = reports_out / f"{args.ticker}_{args.date}_KR.md"
    translated_md.write_text(md_text, encoding="utf-8")
    print(f"\n한글 MD 저장: {translated_md}")

    out_path = reports_out / f"{args.ticker}_{args.date}_KR.docx"
    title = f"TradingAgents 분석 리포트 — {args.ticker} ({args.date})"
    md_to_docx(md_text, out_path, title=title)
    print(f"한글 DOCX 저장: {out_path}")


if __name__ == "__main__":
    main()
