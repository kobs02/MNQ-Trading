"""18차: 여러 봉 단위 MACD 기울기 동시 반전(10-19 사용자 아이디어).

규칙: 1·5·15·60분봉마다 MACD 선(EMA 12 - EMA 26, 종가)의 기울기(앞 봉과의 차)가 음 → 양으로 바뀐 봉을 '위로 꺾임',
  양 → 음을 '아래로 꺾임'으로 본다. 그 봉 단위의 마지막 마감 봉이 꺾인 봉이면 그 단위는 지금 꺾인 상태다
  (1분봉은 1분, 60분봉은 1시간 동안).
  ※ 10-19 저녁 정의 변경(strategies.MACD_HOLD): 꺾인 뒤 기울기가 그 방향인 동안, 1분봉 최대 5분 · 5분봉 15분 · 15·60분봉 60분까지 꺾인 상태.
    이 스크립트는 strategies.macd_counts를 쓰므로 지금 돌리면 새 정의의 결과가 나온다(처음 정의의 결과는 보고서 14절). 1분봉이 마감될 때 위로 꺾인 단위가 need개 이상이고 아래로 꺾인 단위가 없으면 매수 신호
  (반대면 매도 신호). 신호는 새로 꺾인 단위가 생긴 그 분에만 낸다. 다음 1분봉 시가에 들어간다.
청산 변형: 반대 신호까지(뒤집기) / 고정 보유 H분. 방향 변형: 롱만 / 롱·숏.
비용: 왕복 2.5포인트(증권사 체결 1계약당 $1 × 2 + 대회 차감 $1 × 2 = $4 = 2포인트, 미끄러짐 매번 1틱).

통과 기준 (결과를 보기 전에 정함. 1분봉 앞 절반 = 설계에서 변형 하나를 고르고, 뒤 절반 = 확인으로 판정)
  1. 고른 변형의 확인 구간 비용 뒤 합계 손익 > 0
  2. 같은 변형의 설계 구간도 > 0
  3. 확인 구간에서 같은 수·같은 보유 시간·같은 방향 비율의 무작위 진입(200번)보다 95% 이상 좋다
  (r5 ~ r7에서 1분봉 스캘핑은 앞뒤가 뒤집혔다. 자료가 5주 남짓이라 통과해도 관찰용으로만 쓴다)

    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r18_macd_mtf.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import strategies

COST = 2.5
M = strategies.minute_history()  # 뉴욕 시각, 봉 시작 색인


def signals(frame, need):
    """1분봉마다(마감 시각 기준) 신호 +1·-1·0: 새로 꺾인 단위가 생긴 분에, 같은 방향 need개 이상·반대 0개. 계산은 차트와 같은 strategies.macd_counts."""
    up, dn, fresh, _ = strategies.macd_counts(frame)
    sig = np.where((up >= need) & (dn == 0), 1, np.where((dn >= need) & (up == 0), -1, 0))
    return np.where(fresh, sig, 0)


def trades(frame, sig, exit_mode, long_only):
    """[(진입 위치, 청산 위치, 방향)]: 신호 다음 봉 시가에 들고, 반대 신호(그 다음 봉 시가) 또는 H분 뒤 시가에 판다."""
    o, t = frame["open"].to_numpy(), frame.index
    out, i, n = [], 0, len(frame)
    while i < n - 1:
        side = sig[i]
        if not side or (long_only and side < 0):
            i += 1; continue
        a = i + 1
        if exit_mode == "flip":
            j = a
            while j < n - 1 and sig[j] != -side:
                j += 1
            b = j + 1
        else:
            b = int(np.searchsorted(t, t[a] + pd.Timedelta(minutes=exit_mode)))
        if b >= n:
            break
        out.append((a, b, side))
        i = b - 1 if exit_mode == "flip" else b
    return out


def score(frame, tr):
    o = frame["open"].to_numpy()
    pnl = np.array([s * (o[b] - o[a]) - COST for a, b, s in tr])
    return pnl


def random_test(frame, tr, runs=200, seed=0):
    """같은 수·같은 보유 길이·같은 방향 비율로 무작위 진입했을 때 합계 손익들."""
    rng = np.random.default_rng(seed)
    o, n = frame["open"].to_numpy(), len(frame)
    holds = np.array([b - a for a, b, _ in tr]); sides = np.array([s for _, _, s in tr])
    out = []
    for _ in range(runs):
        a = rng.integers(0, n - holds.max() - 1, len(tr))
        out.append(float(np.sum(rng.permutation(sides) * (o[a + holds] - o[a]) - COST)))
    return np.array(out)


# 자르기 점검: 앞쪽만 잘라 계산한 신호 = 전체로 계산한 신호의 앞쪽
cut = len(M) // 3
for need in (2, 3):
    full, part = signals(M, need)[:cut - 200], signals(M.iloc[:cut], need)[:cut - 200]
    assert (full == part).all(), "미래 정보 누출"
print("자르기 점검 통과")

days = M.index.normalize().unique()
mid = days[len(days) // 2]
DES, CON = M[M.index < mid], M[M.index >= mid]
print(f"1분봉 {M.index[0]:%Y-%m-%d} ~ {M.index[-1]:%Y-%m-%d} · 설계 ~ {mid:%m-%d} 전 ({len(DES):,}봉) · 확인 {mid:%m-%d} ~ ({len(CON):,}봉)\n")

rows = []
for need in (2, 3, 4):
    for exit_mode in ("flip", 5, 15, 30, 60):
        for long_only in (True, False):
            r = {"need": need, "exit": exit_mode, "dir": "롱만" if long_only else "롱·숏"}
            for name, f in (("설계", DES), ("확인", CON)):
                p = score(f, trades(f, signals(f, need), exit_mode, long_only))
                r[f"{name} 건수"] = len(p)
                r[f"{name} 합계pt"] = round(float(p.sum()), 1) if len(p) else 0.0
                r[f"{name} 건당pt"] = round(float(p.mean()), 2) if len(p) else 0.0
                r[f"{name} 승률"] = round(float((p > 0).mean()), 2) if len(p) else 0.0
                r[f"{name} 비용전 건당pt"] = round(float(p.mean() + COST), 2) if len(p) else 0.0
            rows.append(r)
T = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print(T.to_string(index=False))

best = T[T["설계 건수"] >= 20].sort_values("설계 합계pt", ascending=False).iloc[0]
print(f"\n설계에서 고른 변형: 꺾인 단위 {best['need']}개 이상 · 청산 {best['exit']} · {best['dir']}")
tr = trades(CON, signals(CON, int(best["need"])), best["exit"] if best["exit"] == "flip" else int(best["exit"]), best["dir"] == "롱만")
p = score(CON, tr)
rand = random_test(CON, tr)
beat = float((p.sum() > rand).mean())
print(f"확인: {len(p)}건, 합계 {p.sum():+.1f}pt (1계약 ${p.sum() * 2:+,.0f}), 건당 {p.mean():+.2f}pt, 무작위 200번보다 나은 비율 {beat:.0%} (무작위 중앙 {np.median(rand):+.1f}pt)")
ok = [p.sum() > 0, best["설계 합계pt"] > 0, beat >= 0.95]
print(f"기준 1 확인 > 0: {ok[0]} · 기준 2 설계 > 0: {ok[1]} · 기준 3 무작위 95%: {ok[2]} → {'통과' if all(ok) else '불통과'}")


# ---------- 추가 관찰 (판정에 안 씀): 꺾인 단위 3개 이상, 그리고 RSI 과매도·과매수일 때만(10-19 사용자 요청 '더 강한 근거') ----------
rsi = strategies.tf_rsi  # 그 봉 단위 RSI 14를 1분봉 마감 시각에 맞춘 값


print("\n추가 관찰(판정 아님): 신호를 RSI로 거르기. 강 = 롱은 RSI < 30, 숏은 RSI > 70 (그 봉 단위 RSI 14)")
obs = []
for need in (2, 3):
    for rtf in (None, 1, 5, 15):
        for exit_mode in (15, 30, 60):
            for long_only in (True, False):
                r = {"need": need, "RSI": "-" if rtf is None else f"{rtf}분", "exit": exit_mode, "dir": "롱만" if long_only else "롱·숏"}
                for name, f in (("설계", DES), ("확인", CON)):
                    s = signals(f, need)
                    if rtf is not None:
                        x = rsi(f, rtf)
                        s = np.where(((s > 0) & (x < 30)) | ((s < 0) & (x > 70)), s, 0)
                    tr = trades(f, s, exit_mode, long_only)
                    p = score(f, tr)
                    r[f"{name} 건수"] = len(p)
                    r[f"{name} 합계pt"] = round(float(p.sum()), 1)
                    r[f"{name} 건당pt"] = round(float(p.mean()), 2) if len(p) else 0.0
                    r[f"{name} 무작위보다"] = f"{(p.sum() > random_test(f, tr, 300)).mean():.0%}" if len(p) >= 5 else "-"
                obs.append(r)
print(pd.DataFrame(obs).to_string(index=False))
print(f"\n기간 MNQ {M.close.iloc[0]:,.2f} → {M.close.iloc[-1]:,.2f} (롱만 변형은 오른 시장의 덕을 본다: 무작위 비교가 그걸 뺀다)")
