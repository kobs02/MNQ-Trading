// 차트 아래 손잡이(.chart-grip)를 끌어 차트 높이를 바꾼다. 높이는 --h-<차트 id> 변수로 두어
// 화면을 다시 그려도(API Trading 탭) 유지되고, 브라우저에 저장해 다음에 열어도 그대로다.

(function () {
  const root = document.documentElement;
  const MIN = 200;
  const key = (id) => 'chart-h-' + id;
  const set = (id, h) => {
    h = Math.max(MIN, Math.round(h));
    root.style.setProperty('--h-' + id, h + 'px');
    try { localStorage.setItem(key(id), h); } catch (e) {}
  };
  for (const id of ['paper-chart', 'pnl-chart']) {
    try { const h = localStorage.getItem(key(id)); if (h) root.style.setProperty('--h-' + id, h + 'px'); } catch (e) {}
  }

  document.addEventListener('pointerdown', (e) => {
    const grip = e.target.closest('.chart-grip');
    if (!grip) return;
    const chart = grip.previousElementSibling;
    const startY = e.clientY, startH = chart.offsetHeight;
    grip.setPointerCapture(e.pointerId);
    const move = (ev) => set(chart.id, startH + ev.clientY - startY);
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', () => grip.removeEventListener('pointermove', move), { once: true });
    e.preventDefault();
  });
  document.addEventListener('keydown', (e) => {
    const grip = e.target.closest && e.target.closest('.chart-grip');
    if (!grip || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
    const chart = grip.previousElementSibling;
    set(chart.id, chart.offsetHeight + (e.key === 'ArrowDown' ? 40 : -40));
    e.preventDefault();
  });
})();
