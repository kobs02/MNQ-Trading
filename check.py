"""연결 점검: 토큰, 예탁자산, 미결제 포지션, MNQ 근월물 시세와 최근 분봉을 읽기만 한다(주문은 내지 않는다).

    .venv\\Scripts\\python check.py
"""
import sys

from mnq import ls

sys.stdout.reconfigure(encoding="utf-8")

ls.token()
print("토큰: 받음")

rows, total = ls.assets()
for r in rows:
    print(f"예탁자산 {r['CrcyCode']}: 예수금 {r['OvrsFutsDps']} · 주문가능 {r['AbrdFutsOrdAbleAmt']} · 평가손익 {r['AbrdFutsEvalPnlAmt']}")

net, _ = ls.positions()
print("미결제 포지션:", net or "없음")

m = ls.front_month()
print(f"근월물: {m['Symbol']} {m['SymbolNm']} · 1틱 {m['UntPrc']} = ${m['MnChgAmt']} · 개시증거금 ${m['OpngMgn']} · 거래 {m['DlStrtTm']}~{m['DlEndTm']}")
q = ls.quote(m["Symbol"])
print(f"현재가: {q['TrdP']} (전일 대비 {q['YdiffP']}) · 만기 {q['MtrtDt']} · 한국 시각 {q['KorDate']} {q['RcvTm']}")

bars = ls.minute_bars(m["Symbol"], minutes=1, count=10)
for b in bars[-5:]:
    print(f"  {b['date']} {b['time']}  시 {b['open']} 고 {b['high']} 저 {b['low']} 종 {b['close']} 량 {b['volume']}")
