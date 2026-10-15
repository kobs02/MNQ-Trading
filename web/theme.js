// 밝게/어둡게 전환: 맨 위 줄에 전환 버튼을 달고, 페이지 스크립트가 만든 차트의 글자·격자 색을 테마에 맞춘다.

(function () {
  const root = document.documentElement;
  const token = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  function paintChart() {
    if (typeof chart === 'undefined') return;  // 차트가 없는 페이지(뉴스)
    chart.applyOptions({
      layout: { textColor: token('--text-2'), fontSize: 13 },
      grid: { vertLines: { color: token('--grid') }, horzLines: { color: token('--grid') } },
      rightPriceScale: { borderColor: token('--border') },
      timeScale: { borderColor: token('--border') },
    });
  }

  const header = document.querySelector('body > header, #chart-col > header');
  const tools = document.createElement('span');
  tools.className = 'page-tools';
  tools.innerHTML = '<button type="button" id="theme-toggle"></button>';
  header.append(tools);
  const button = tools.querySelector('button');

  function apply(theme) {
    root.dataset.theme = theme;
    localStorage.setItem('theme', theme);
    button.textContent = theme === 'dark' ? '밝게' : '어둡게';
    button.setAttribute('aria-label', theme === 'dark' ? '밝은 화면으로 바꾸기' : '어두운 화면으로 바꾸기');
    paintChart();
    // 자산 변화 페이지: 첫 번째 선이 흰색이라 밝은 화면에서 안 보이므로 글자 색으로 바꿔 다시 그린다
    if (typeof PALETTE !== 'undefined' && typeof render === 'function') {
      PALETTE[0] = token('--text');
      render();
    }
    document.dispatchEvent(new Event('themechange'));  // 선 색을 직접 정하는 페이지가 다시 그리게 한다
  }
  button.addEventListener('click', () => apply(root.dataset.theme === 'dark' ? 'light' : 'dark'));
  // 주소에 ?theme=light 를 붙이면 그 테마로 연다
  const asked = new URLSearchParams(location.search).get('theme');
  apply((asked || root.dataset.theme) === 'light' ? 'light' : 'dark');
})();
