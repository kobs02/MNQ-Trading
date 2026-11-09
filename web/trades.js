// 주문 기록·타임랩스가 같이 쓰는 체결 계산
// 체결마다: 같은 방향이면 평균 진입가를 옮기고, 반대 방향이면 닫은 몫의 손익을 낸다(넘치면 반대로 새로 연다)
function walk(orders, mult, fee) {
  let pos = 0, avg = 0;
  return orders.map((o) => {
    const qty = o.exec_qty != null ? Math.sign(o.qty) * o.exec_qty : o.ok && !o.limit ? o.qty : 0;  // LS 체결 수량이 없으면 시장가는 다 체결, 지정가는 안 됐다고 본다(걸려만 있던 지정가를 체결로 보면 포지션이 어긋난다)
    const px = o.exec_price || o.price;
    let realized = null, ret = null, closed = 0;
    if (qty && px) {
      if (pos && Math.sign(qty) !== Math.sign(pos)) {
        closed = Math.min(Math.abs(qty), Math.abs(pos));
        realized = closed * Math.sign(pos) * (px - avg) * mult;
        ret = Math.sign(pos) * (px - avg) / avg;  // 평균 진입가 대비(숏은 내리면 +)
        const left = pos + qty;
        avg = Math.sign(left) === Math.sign(pos) || !left ? avg : px;  // 뒤집혔으면 넘친 몫은 체결가에 새로 연다
        pos = left;
      } else {
        avg = (avg * Math.abs(pos) + px * Math.abs(qty)) / (Math.abs(pos) + Math.abs(qty));
        pos += qty;
      }
    }
    return { ...o, filled: qty, px, realized, ret, closed, fee: Math.abs(qty) * 2 * fee, pos, avg };
  });
}
