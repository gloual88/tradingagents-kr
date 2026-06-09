"""dashboard.py — TradingAgents Streamlit 대시보드 (CLI TUI 대체)

CLI(`tradingagents`)의 인터랙티브 TUI를 웹 대시보드로 감쌌다. 티커/날짜/깊이/분석가를
고르고 [분석 실행]을 누르면 멀티에이전트 그래프를 스트리밍으로 돌려 라운드별 진행률·
토큰 사용량을 실시간 표시하고, 완료 후 7개 리포트와 최종 매매 신호를 탭으로 보여준다.

- LLM 호출은 NormalizedChatAnthropic 한 곳을 지나며, 이미 적용한 토큰 절감(리포트
  다이제스트 + get_stock_data CSV 압축)이 그대로 작동한다.
- 그래프 _log_state 는 encoding=utf-8 → cp949 오류 없음(CLI main.py 의 버그와 무관).

실행(PowerShell):
  d:\파이선\pykrx_venv\Scripts\streamlit.exe run `
    d:\파이선\kr_dashboard\tradingagents\dashboard.py --server.port 8530
"""
from __future__ import annotations

import os
import shutil
import tempfile
import warnings
from datetime import date
from pathlib import Path

warnings.simplefilter("ignore")

HERE = Path(__file__).resolve().parent


# ----------------------------------------------------------------- 환경 준비
def _load_env() -> None:
    """ANTHROPIC_API_KEY / TRADINGAGENTS_* 를 .env 에서 로드(없으면 폴백)."""
    for p in (HERE / ".env", HERE.parents[1] / ".env"):  # tradingagents/.env → 파이선/.env
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fix_ssl() -> None:
    """한글 경로 certifi SSL 실패 방지 — cacert 를 %TEMP% 로 복사 후 환경변수 지정."""
    try:
        import certifi
        dst = os.path.join(tempfile.gettempdir(), "cacert_ta_dash.pem")
        if not os.path.exists(dst):
            shutil.copyfile(certifi.where(), dst)
        for k in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            os.environ[k] = dst
    except Exception:
        pass


_load_env()
_fix_ssl()

import streamlit as st  # noqa: E402

ANALYSTS = [("market", "시장(기술적)"), ("social", "소셜 센티먼트"),
            ("news", "뉴스/매크로"), ("fundamentals", "펀더멘털")]
DEPTH = {"Shallow (빠름·저비용, 1라운드)": 1, "Medium (2라운드)": 2, "Deep (3라운드)": 3}


# ----------------------------------------------------------------- 그래프 구동
def build_config(provider, deep, quick, depth, effort):
    from tradingagents.default_config import DEFAULT_CONFIG
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = provider
    cfg["deep_think_llm"] = deep
    cfg["quick_think_llm"] = quick
    cfg["backend_url"] = None if provider == "anthropic" else cfg.get("backend_url")
    cfg["max_debate_rounds"] = depth
    cfg["max_risk_discuss_rounds"] = depth
    cfg["anthropic_effort"] = effort or None
    return cfg


def run_pipeline(ticker, trade_date, analysts, cfg, on_progress):
    """그래프를 스트리밍 구동. on_progress(state, stats) 를 청크마다 호출.
    (final_state, decision, stats) 반환."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from cli.stats_handler import StatsCallbackHandler

    stats = StatsCallbackHandler()
    graph = TradingAgentsGraph(analysts, config=cfg, debug=False,
                               callbacks=[stats])
    init_state = graph.propagator.create_initial_state(ticker, trade_date)
    args = graph.propagator.get_graph_args(callbacks=[stats])

    final_state = init_state
    for chunk in graph.graph.stream(init_state, **args):
        final_state = chunk
        on_progress(final_state, stats.get_stats())

    decision = graph.process_signal(final_state.get("final_trade_decision", ""))
    return final_state, decision, stats.get_stats()


# 진행 단계(상태 키 존재 여부로 완료 판정)
STAGES = [
    ("market_report", "📈 시장 분석"),
    ("sentiment_report", "💬 센티먼트 분석"),
    ("news_report", "📰 뉴스 분석"),
    ("fundamentals_report", "📊 펀더멘털 분석"),
    ("__bull_bear", "🐂🐻 연구원 토론"),
    ("__research_mgr", "🧑‍⚖️ 리서치 매니저 판단"),
    ("trader_investment_plan", "💼 트레이더 계획"),
    ("__risk", "⚖️ 리스크 토론"),
    ("final_trade_decision", "✅ 최종 결정"),
]


def stage_done(state, key) -> bool:
    if key == "__bull_bear":
        d = state.get("investment_debate_state") or {}
        return bool(d.get("bull_history") and d.get("bear_history"))
    if key == "__research_mgr":
        d = state.get("investment_debate_state") or {}
        return bool(d.get("judge_decision"))
    if key == "__risk":
        d = state.get("risk_debate_state") or {}
        return bool(d.get("judge_decision"))
    return bool(state.get(key))


# ----------------------------------------------------------------- UI
st.set_page_config(page_title="TradingAgents 대시보드", page_icon="📊",
                   layout="wide")


def _check_password() -> None:
    """외부 공개 시 API 예산 보호용 비밀번호 게이트.

    비밀번호는 환경변수 TA_DASH_PASSWORD 또는 .streamlit/secrets.toml 의
    dash_password 로 설정. 둘 다 없으면 게이트 비활성(로컬 전용) — 외부에
    노출할 때는 반드시 설정하세요.
    """
    expected = os.environ.get("TA_DASH_PASSWORD", "")
    if not expected:
        try:
            expected = st.secrets.get("dash_password", "")
        except Exception:
            expected = ""
    if not expected:
        st.sidebar.warning("🔓 비밀번호 미설정 — 로컬 전용. 외부 공개 시 "
                           "TA_DASH_PASSWORD 를 설정하세요.")
        return
    if st.session_state.get("auth_ok"):
        return
    st.title("🔒 TradingAgents 대시보드")
    pw = st.text_input("접속 비밀번호", type="password")
    if pw and pw == expected:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pw:
        st.error("비밀번호가 틀렸습니다.")
    st.stop()


_check_password()

st.title("📊 TradingAgents 멀티에이전트 분석")
st.caption("CLI(TUI)를 웹 대시보드로 — 분석가→토론→리스크→결정 파이프라인. "
           "토큰 절감(리포트 다이제스트·CSV 압축) 적용본.")

with st.sidebar:
    st.header("분석 설정")
    ticker = st.text_input("티커", value="005930.KS",
                           help="한국: 005930.KS(삼성전자), 069500.KS(KODEX200) · "
                                "미국: AAPL, SPY · 지수: ^KS11")
    trade_date = st.date_input("분석 기준일", value=date.today())
    depth_label = st.selectbox("분석 깊이", list(DEPTH.keys()), index=0)
    picked = st.multiselect("분석가 선택", [a[0] for a in ANALYSTS],
                            default=[a[0] for a in ANALYSTS],
                            format_func=lambda k: dict(ANALYSTS)[k],
                            help="적게 고르면 빠르지만 커버리지가 줄어듭니다.")
    benchmark = st.text_input("벤치마크 티커", value="^KS11",
                              help="한국 종목은 ^KS11 권장. 미국은 비워도 됨.")
    language = st.selectbox("리포트 언어", ["Korean", "English"], index=0)

    with st.expander("모델 (고급)"):
        provider = st.text_input(
            "provider", os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "anthropic"))
        deep = st.text_input(
            "deep_think (토론·판단)",
            os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "claude-sonnet-4-6"))
        quick = st.text_input(
            "quick_think (분석가)",
            os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM",
                           "claude-haiku-4-5-20251001"))
        effort = st.text_input("anthropic_effort (선택)", "")

    run = st.button("🚀 분석 실행", type="primary", use_container_width=True)
    st.caption("⏱ Shallow 기준 1티커 5~10분. 실행 중 탭을 닫지 마세요.")

if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error("ANTHROPIC_API_KEY 가 없습니다 — .env(d:\\파이선\\.env) 확인.")
    st.stop()

if run:
    if not picked:
        st.warning("분석가를 1개 이상 선택하세요.")
        st.stop()
    analysts = [k for k, _ in ANALYSTS if k in picked]  # 고정 순서
    if benchmark.strip():
        os.environ["TRADINGAGENTS_BENCHMARK_TICKER"] = benchmark.strip()
    os.environ["TRADINGAGENTS_OUTPUT_LANGUAGE"] = language
    cfg = build_config(provider.strip(), deep.strip(), quick.strip(),
                       DEPTH[depth_label], effort.strip())

    prog = st.progress(0.0, text="시작…")
    stage_box = st.empty()
    metric_box = st.empty()

    def on_progress(state, stats):
        done = [(lbl, stage_done(state, k)) for k, lbl in STAGES]
        n = sum(1 for _, d in done if d)
        prog.progress(n / len(STAGES),
                      text=f"진행 {n}/{len(STAGES)} 단계")
        stage_box.markdown(
            "  ".join(f"{'✅' if d else '⬜'} {lbl}" for lbl, d in done))
        c = metric_box.columns(4)
        c[0].metric("LLM 호출", stats["llm_calls"])
        c[1].metric("도구 호출", stats["tool_calls"])
        c[2].metric("입력 토큰", f"{stats['tokens_in']:,}")
        c[3].metric("출력 토큰", f"{stats['tokens_out']:,}")

    try:
        with st.spinner(f"{ticker} 분석 중… (모든 에이전트 순차 실행)"):
            final_state, decision, stats = run_pipeline(
                ticker, trade_date.isoformat(), analysts, cfg, on_progress)
        st.session_state["result"] = {
            "ticker": ticker, "date": trade_date.isoformat(),
            "state": final_state, "decision": decision, "stats": stats}
        prog.progress(1.0, text="완료 ✅")
        st.success("분석 완료")
    except Exception as e:  # noqa: BLE001
        st.exception(e)
        st.stop()

# ----------------------------------------------------------------- 결과 표시
res = st.session_state.get("result")
if res:
    state, decision, stats = res["state"], res["decision"], res["stats"]
    sig = (decision or "").upper()
    color = ("🟢" if "BUY" in sig else "🔴" if "SELL" in sig
             else "🟡" if "HOLD" in sig else "⚪")
    st.subheader(f"{res['ticker']} · {res['date']}")
    m = st.columns(4)
    m[0].metric("최종 신호", f"{color} {decision or '-'}")
    m[1].metric("LLM 호출", stats["llm_calls"])
    m[2].metric("입력 토큰", f"{stats['tokens_in']:,}")
    m[3].metric("출력 토큰", f"{stats['tokens_out']:,}")

    inv = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}
    tabs = st.tabs(["시장", "센티먼트", "뉴스", "펀더멘털",
                    "연구원 토론", "트레이더", "리스크 토론", "최종 결정"])
    with tabs[0]:
        st.markdown(state.get("market_report") or "_없음_")
    with tabs[1]:
        st.markdown(state.get("sentiment_report") or "_없음_")
    with tabs[2]:
        st.markdown(state.get("news_report") or "_없음_")
    with tabs[3]:
        st.markdown(state.get("fundamentals_report") or "_없음_")
    with tabs[4]:
        st.markdown("#### 🐂 강세론\n" + (inv.get("bull_history") or "_없음_"))
        st.markdown("#### 🐻 약세론\n" + (inv.get("bear_history") or "_없음_"))
        if inv.get("judge_decision"):
            st.markdown("#### 🧑‍⚖️ 리서치 매니저 종합\n" + inv["judge_decision"])
    with tabs[5]:
        st.markdown(state.get("trader_investment_plan") or "_없음_")
    with tabs[6]:
        st.markdown(risk.get("history") or "_없음_")
        if risk.get("judge_decision"):
            st.markdown("#### ⚖️ 리스크 매니저 종합\n" + risk["judge_decision"])
    with tabs[7]:
        st.markdown(state.get("final_trade_decision") or "_없음_")

    # 다운로드 — 통합 마크다운
    parts = [f"# TradingAgents — {res['ticker']} ({res['date']})",
             f"\n## 최종 신호: {decision}\n"]
    for key, title in [("market_report", "시장"), ("sentiment_report", "센티먼트"),
                       ("news_report", "뉴스"), ("fundamentals_report", "펀더멘털"),
                       ("trader_investment_plan", "트레이더 계획"),
                       ("final_trade_decision", "최종 결정")]:
        if state.get(key):
            parts.append(f"\n## {title}\n\n{state[key]}")
    st.download_button("📥 통합 리포트(.md) 다운로드", "\n".join(parts),
                       file_name=f"{res['ticker']}_{res['date']}.md",
                       mime="text/markdown")
else:
    st.info("← 좌측에서 티커·날짜를 설정하고 [분석 실행]을 누르세요.")
