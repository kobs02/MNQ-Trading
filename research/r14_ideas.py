"""14차: 세 관점(트레이더, 경제학·수학, 창의)에서 아직 시험하지 않은 일봉 아이디어 6갈래. 기준은 되돌림 + 추세(dip_trend).

갈래 (r1 ~ r13에서 안 한 것만)
  A 월말·월초(turn of month): 월 마지막 n1일 + 첫 n2일 롱. 연기금·급여 자금 유입(Ariel 1987, McConnell·Xu 2008)
  B 밤사이 보유: 종가에 사서 다음 날 시가에 판다. 지수 수익이 밤사이에 몰린다(Kelly·Clark 2011, r2에서도 확인)
  C IBS: (종가-저가)/(고가-저가)가 낮게 끝난 날 다음 하루 롱(지수 단기 되돌림, 저가 근처 마감 = 매도 소진)
  D 변동성 위험 프리미엄: VIX / 실현 변동성(20일)이 높을 때(공포가 실제보다 과할 때) 롱(Bollerslev·Tauchen·Zhou 2009)
  E 연속 하락: n일 연속 하락 뒤 5일 롱(되돌림의 다른 정의)
  F 대회용 이익 잠금: 4주 구간 안에서 수익이 g를 넘으면 그 뒤 노출을 반으로(점수 = 수익 + MDD일 때 낙폭을 지키는 쪽)
  각 갈래는 단독(추세 200일선 위에서만)과 "되돌림 + 추세와 합치기(둘 중 하나라도 켜지면 1배)"를 함께 본다.

통과 기준 (결과를 보기 전에 정함. 설계 2001 ~ 2019에서 갈래마다 변형 하나를 고르고, 확인 2020 ~ 으로 판정)
  1. 확인 구간 4주 수익+MDD 평균이 기준보다 0.20%p 이상 좋다
  2. 확인 구간 샤프가 기준 이상
  3. 설계 구간 4주 수익+MDD 평균도 기준보다 좋다(확인 구간에서만 좋은 우연 거르기)
  4. 신호를 무작위로 밀어 낸(circular shift, 200번) 같은 꼴의 신호보다 확인 구간 수익+MDD가 95% 이상 좋다
     (노출이 적어서 낙폭이 준 효과와 진짜 우위를 가른다. r9의 교훈)
  F는 신호가 아니라 노출 조절이라 4 대신 "무작위 시점에 반으로 줄이기"와 비교한다.

체결: t일 종가(밤사이 보유는 시가)에 정한 노출을 다음 수익률에 적용, 비용은 노출 변화 1배당 0.01%.
    cd C:\\dev\\MNQ-Trader && .venv\\Scripts\\python research\\r14_ideas.py
"""
import sys
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from mnq import config, strategies

ndx = pd.read_pickle(config.STORE / "data" / "NDX_1d.pkl").loc["1999":].dropna()
vix = pd.read_pickle(config.STORE / "data" / "macro_1d.pkl")["VIX"].reindex(ndx.index).ffill()
C, O, H, L = ndx.Close, ndx.Open, ndx.High, ndx.Low
cc = C.pct_change().fillna(0.0)          # 종가 → 종가
on = (O / C.shift() - 1).fillna(0.0)     # 종가 → 다음 날 시가(밤사이)
COST = strategies.COST
DESIGN, CONFIRM = ("2001", "2019"), ("2020", "2026")
up = C > C.rolling(200).mean()
dip = strategies.RULES["dip_trend"](C).fillna(0.0)


def pnl_cc(expo):
    expo = expo.reindex(C.index).fillna(0.0)
    return expo.shift(1).fillna(0.0) * cc - COST * expo.diff().abs().fillna(0.0)


def pnl_on(expo):
    """밤사이 보유: 종가에 들고 다음 시가에 판다. 날마다 왕복 비용."""
    held = expo.reindex(C.index).fillna(0.0).shift(1).fillna(0.0)
    return held * on - 2 * COST * held.abs()


def score(pnl, span):
    p = pnl.loc[span[0]:span[1]]
    w = strategies.windows(p)
    return w["score"], p.mean() / p.std() * np.sqrt(252)


# ---------- 갈래별 변형 ----------
def tom(n1, n2):
    month = pd.Series(C.index.to_period("M"), index=C.index)
    pos = month.groupby(month).cumcount()
    left = month.groupby(month).transform("size") - pos  # 이 달 남은 거래일(마지막 날 = 1)
    # t일 종가에 정한 노출이 t+1일에 적용되므로, t+1이 '마지막 n1일 또는 첫 n2일'이면 t에 켠다
    nxt = ((left <= n1) | (pos < n2)).shift(-1, fill_value=False)
    return nxt.astype(float)


def ibs(x):
    v = (C - L) / (H - L).replace(0, np.nan)
    return (v < x).astype(float)


def vrp(q):
    rv = cc.rolling(20).std() * np.sqrt(252) * 100
    return (vix / rv > q).astype(float)


def downs(n):
    d = (cc < 0).astype(int)
    run = d.groupby((d == 0).cumsum()).cumsum()
    return (run >= n).astype(float).rolling(5).max()


FAMILIES = {
    "A 월말·월초": {f"{a},{b}": tom(a, b) for a in (1, 2) for b in (2, 3, 4)},
    "C IBS": {f"<{x}": ibs(x) for x in (0.15, 0.2, 0.25, 0.3)},
    "D VIX/실현변동성": {f">{q}": vrp(q) for q in (1.1, 1.3, 1.5, 1.8)},
    "E 연속 하락": {f"{n}일": downs(n) for n in (2, 3, 4)},
}


def variants():
    """(갈래, 변형 이름, 일별 손익 함수, 노출 신호). 신호는 무작위 밀기 점검에 다시 쓴다."""
    out = []
    for fam, grid in FAMILIES.items():
        for name, sig in grid.items():
            out.append((fam + " 단독", name, lambda s: pnl_cc(s * up), sig))
            out.append((fam + " + 되돌림", name, lambda s: pnl_cc(np.maximum(s * up, dip)), sig))
    for name, filt in (("늘", pd.Series(1.0, index=C.index)), ("추세 위", up.astype(float))):
        out.append(("B 밤사이 단독", name, lambda s: pnl_on(s), filt))
        # 되돌림이 켜진 날은 종가→종가로 들고, 나머지 날은 밤사이만
        out.append(("B 밤사이 + 되돌림", name, lambda s: pnl_cc(dip) + pnl_on(s * (dip == 0)), filt))
    return out


def lockin(g, cut, rng=None):
    """F: 4주 구간마다 되돌림 + 추세를 돌리다 구간 수익이 g를 넘으면 그 뒤 노출을 cut배로. rng가 있으면 무작위 날에 줄인다(비교용)."""
    base = pnl_cc(dip)
    def run(span):
        p = base.loc[span[0]:span[1]].to_numpy()
        rows = []
        for i in range(0, len(p) - 20 + 1, 5):
            eq, peak, low, scale = 1.0, 1.0, 0.0, 1.0
            k = rng.integers(0, 20) if rng is not None else None
            for j, x in enumerate(p[i:i + 20]):
                eq *= 1 + x * scale
                peak = max(peak, eq)
                low = min(low, eq / peak - 1)
                if (k is None and eq - 1 >= g) or (k is not None and j == k):
                    scale = cut
            rows.append(eq - 1 + low)
        return float(np.mean(rows))
    return run


if __name__ == "__main__":
    base_d, base_ds = score(pnl_cc(dip), DESIGN)
    base_c, base_cs = score(pnl_cc(dip), CONFIRM)
    print(f"기준 되돌림 + 추세: 설계 수익+MDD {base_d:+.2%} 샤프 {base_ds:.2f} | 확인 수익+MDD {base_c:+.2%} 샤프 {base_cs:.2f}\n")
    rng = np.random.default_rng(0)
    rows = {}
    for fam, name, f, sig in variants():
        d, _ = score(f(sig), DESIGN)
        rows.setdefault(fam, []).append((d, name, f, sig))
    print("갈래                  고른 변형 | 설계 수익+MDD | 확인 수익+MDD  샤프 | 이웃 변형 확인 중앙 | 무작위 밀기 순위 | 1 2 3 4 | 판정")
    for fam, rs in rows.items():
        d, name, f, sig = max(rs, key=lambda r: r[0])
        c, cs = score(f(sig), CONFIRM)
        others = np.median([score(r[2](r[3]), CONFIRM)[0] for r in rs])
        null = [score(f(pd.Series(np.roll(sig.to_numpy(), rng.integers(250, len(sig) - 250)), index=sig.index)), CONFIRM)[0] for _ in range(200)]
        rank = np.mean(np.array(null) < c)
        ok = [c >= base_c + 0.002, cs >= base_cs, d > base_d, rank >= 0.95]
        print(f"{fam:20s} {name:>8s} | {d:+7.2%} | {c:+7.2%} {cs:5.2f} | {others:+7.2%} | {rank:5.0%} | "
              f"{' '.join('O' if x else '.' for x in ok)} | {'통과' if all(ok) else '불통과'}")

    print("\nF 이익 잠금(되돌림 + 추세 위에서)       설계 | 확인 | 무작위 시점에 같은 만큼 줄이기(확인, 50번 평균)")
    for g in (0.01, 0.02, 0.03):
        for cut in (0.5, 0.0):
            d, c = lockin(g, cut)(DESIGN), lockin(g, cut)(CONFIRM)
            rnd = np.mean([lockin(g, cut, np.random.default_rng(s))(CONFIRM) for s in range(50)])
            ok = [c >= base_c + 0.002, d > base_d, c > rnd]
            print(f"  +{g:.0%} 넘으면 {cut:.0%}로   {d:+.2%} | {c:+.2%} | {rnd:+.2%} | {' '.join('O' if x else '.' for x in ok)}")
