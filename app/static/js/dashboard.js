/**
 * AiToEarn 仪表盘前端
 * 方案切换联动刷新所有区域，时间范围仅刷新收益曲线
 */

// 全局状态
let currentScheme = 'B';
let currentRange = 'all';
let currentHistoryScope = 'five_year';
let currentHoldingCodes = new Set();
let currentSellCodes = new Set();
let currentNewEntryCodes = new Set();
let allTradeHistory = [];
let historyShown = 12;

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
  bindEvents();
  loadAll();
});

function bindEvents() {
  // 方案切换 —— 联动所有区域
  document.querySelectorAll('#scheme-toggle .btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#scheme-toggle .btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentScheme = btn.dataset.scheme;
      loadAll();
    });
  });

  // 时间范围 —— 仅刷新收益曲线
  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-range]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentRange = btn.dataset.range;
      loadEquityChart();
    });
  });

  document.querySelectorAll('[data-history-scope]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-history-scope]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentHistoryScope = btn.dataset.historyScope;
      loadHistoricalBacktest();
    });
  });

  // 手动刷新
  document.getElementById('btn-refresh').addEventListener('click', triggerRefresh);

  // 盘中/收盘切换
  document.getElementById('session-select').addEventListener('change', loadRanking);

  // CSV导出
  document.getElementById('btn-export').addEventListener('click', exportCSV);

  // 换仓历史查看更多
  document.getElementById('btn-more-history').addEventListener('click', () => {
    historyShown = allTradeHistory.length;
    renderTradeTimeline();
  });
}

// ========== 并行加载所有数据 ==========
function loadAll() {
  Promise.all([
    loadOverview(),
    loadEquityChart(),
    loadRanking(),
    loadCurrentHoldings(),
    loadTradeHistory(),
    loadStrategyStats(),
    loadHistoricalBacktest(),
  ]).catch(err => console.error('loadAll error:', err));
}

// ========== 五年历史回测 ==========
async function loadHistoricalBacktest() {
  const chartEl = document.getElementById('chart-history');
  if (!chartEl) return;
  try {
    const resp = await fetch(`/api/historical?scheme=${currentScheme}&scope=${currentHistoryScope}`);
    const data = await resp.json();
    if (!resp.ok) {
      chartEl.innerHTML = `<div class="text-muted p-4">${data.error || '历史回测尚未生成'}</div>`;
      return;
    }

    const summary = data.summary || {};
    const quality = data.quality || {};
    document.getElementById('hist-period').textContent =
      `${data.period.start} — ${data.period.end} · 方案${data.scheme}`;
    document.getElementById('hist-final-asset').textContent = '¥' + formatNum(summary.final_asset);
    setMetricPct('hist-cum-return', summary.cumulative_return_pct);
    document.getElementById('hist-max-dd').textContent = `${Number(summary.max_drawdown_pct || 0).toFixed(2)}%`;
    document.getElementById('hist-data-quality').textContent = `${quality.usable || 0}/${quality.requested || 0} 只`;

    Plotly.newPlot('chart-history', [{
      x: data.dates || [], y: data.assets || [], type: 'scatter', mode: 'lines',
      name: `方案${data.scheme}`, line: { color: '#1f78b4', width: 2.4 },
      fill: 'tozeroy', fillcolor: 'rgba(37,99,235,.08)',
      hovertemplate: '%{x|%Y-%m-%d}<br>资产 ¥%{y:,.0f}<extra></extra>',
    }], {
      title: { text: `${currentHistoryScope === 'ytd' ? '2026年30万元独立起算' : '30万元连续复利曲线'} · 方案${data.scheme}`, font: { size: 14 } },
      xaxis: { tickformat: '%Y-%m', gridcolor: '#edf1f4' },
      yaxis: { title: '账户资产（元）', tickformat: ',.0f', gridcolor: '#edf1f4' },
      hovermode: 'x', template: 'plotly_white', showlegend: false,
      margin: { l: 65, r: 20, t: 45, b: 45 }, height: 430,
    }, { responsive: true, displaylogo: false });

    const annualBody = document.getElementById('history-annual-body');
    annualBody.innerHTML = '';
    (data.annual || []).forEach(row => {
      const tr = document.createElement('tr');
      const returnClass = row.return_pct >= 0 ? 'return-up' : 'return-down';
      tr.innerHTML = `<td><strong>${row.year}${row.is_partial ? ' YTD' : ''}</strong></td>
        <td>¥${formatNum(row.start_asset)}</td><td>¥${formatNum(row.end_asset)}</td>
        <td class="${returnClass}">${row.return_pct >= 0 ? '+' : ''}${row.return_pct.toFixed(2)}%</td>
        <td class="return-down">${row.max_drawdown_pct.toFixed(2)}%</td>`;
      annualBody.appendChild(tr);
    });

    document.getElementById('history-quality').innerHTML = `
      <div class="quality-item"><span>可用 ETF</span><strong>${quality.usable || 0} / ${quality.requested || 0}</strong></div>
      <div class="quality-item"><span>完整五年覆盖</span><strong>${quality.full_five_year_coverage || 0} 只</strong></div>
      <div class="quality-item"><span>已处理公司行动</span><strong>${quality.corporate_actions_adjusted || 0} 次</strong></div>
      <div class="quality-item"><span>保留极端行情</span><strong>${quality.large_moves_kept || 0} 次</strong></div>
      <div class="quality-item"><span>交易成本</span><strong>单边 ${(Number(data.methodology.trade_cost_each_side) * 100).toFixed(2)}%</strong></div>
      <div class="quality-item"><span>生成时间</span><strong>${data.generated_at.slice(0, 10)}</strong></div>`;
    document.getElementById('history-limitations').innerHTML =
      `<strong>数据源：</strong>${quality.source || '--'}<br>` +
      (quality.limitations || []).map(x => `• ${x}`).join('<br>');

    const anomalies = data.anomalies || [];
    document.getElementById('anomaly-count').textContent = `${anomalies.length} 条`;
    const anomalyEl = document.getElementById('history-anomalies');
    anomalyEl.innerHTML = '';
    anomalies.slice(0, 40).forEach(item => {
      const div = document.createElement('div');
      div.className = `anomaly-item ${item.action || ''}`;
      const typeLabel = item.type === 'cash_dividend' ? '现金分红' :
        item.type === 'share_split_or_scale_change' ? '份额折算' :
        item.type === 'large_market_move' ? '极端行情' : '数据异常';
      div.innerHTML = `<div class="top"><span>${item.code} ${item.name || ''}</span><span>${item.move_pct == null ? '' : item.move_pct + '%'}</span></div>
        <div class="detail">${item.date} · ${typeLabel} · ${item.action === 'adjusted' ? '已复权' : item.action === 'kept' ? '规则核验后保留' : item.action}</div>`;
      anomalyEl.appendChild(div);
    });
  } catch (e) {
    console.error('loadHistoricalBacktest error:', e);
    chartEl.innerHTML = '<div class="text-muted p-4">历史回测加载失败</div>';
  }
}

function setMetricPct(id, value) {
  const el = document.getElementById(id);
  const n = Number(value || 0);
  el.textContent = `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
  el.className = n >= 0 ? 'return-up' : 'return-down';
}

// ========== 区域2：账户总览 ==========
async function loadOverview() {
  try {
    const resp = await fetch(`/api/equity/v2?scheme=${currentScheme}&range=all`);
    const data = await resp.json();
    const stats = data.stats || {};

    document.getElementById('ov-total-asset').textContent = '¥' + formatNum(stats.total_asset || 300000);

    const cumPnl = stats.cum_pnl || 0;
    const cumPct = stats.cum_return || 0;
    const pnlEl = document.getElementById('ov-cum-pnl');
    const pctEl = document.getElementById('ov-cum-pct');
    pnlEl.textContent = (cumPnl >= 0 ? '+¥' : '-¥') + formatNum(Math.abs(cumPnl));
    pnlEl.className = 'value ' + (cumPnl >= 0 ? 'positive' : 'negative');
    pctEl.textContent = (cumPct >= 0 ? '+' : '') + cumPct.toFixed(2) + '%';
    pctEl.className = 'sub ' + (cumPct >= 0 ? 'positive' : 'negative');

    document.getElementById('ov-max-dd').textContent = (stats.max_dd || 0).toFixed(2) + '%';
    document.getElementById('ov-sharpe').textContent = (stats.sharpe || 0).toFixed(2);

    // 导航栏状态
    const statusEl = document.getElementById('nav-status');
    statusEl.innerHTML = `数据状态: <span class="badge bg-success">最新 ${stats.latest_date || '--'}</span>`;
  } catch (e) {
    console.error('loadOverview error:', e);
  }
}

// ========== 区域4：收益曲线 ==========
async function loadEquityChart() {
  try {
    const resp = await fetch(`/api/equity/v2?scheme=${currentScheme}&range=${currentRange}`);
    const data = await resp.json();

    const dates = data.dates || [];
    const totalAssets = data.total_assets || [];
    const benchmarkAssets = data.benchmark_assets || [];

    if (dates.length === 0) {
      document.getElementById('chart-equity').innerHTML = '<p class="text-center text-muted mt-4">暂无数据</p>';
      return;
    }

    const initialAsset = totalAssets[0] || 300000;
    const initialBenchmark = benchmarkAssets[0] || 300000;

    // 计算较年初的累计收益率（年初基准 = 300000）
    const strategyYtdReturns = totalAssets.map(v => ((v - 300000) / 300000 * 100));
    const benchmarkYtdReturns = benchmarkAssets.map(v => ((v - 300000) / 300000 * 100));

    const traces = [
      {
        x: dates, y: totalAssets, mode: 'lines', name: `方案${currentScheme}策略`,
        line: { color: '#3498db', width: 2.5 },
        customdata: strategyYtdReturns.map(r => r.toFixed(2)),
        hovertemplate: '%{x|%Y-%m-%d}<br>策略: ¥%{y:,.0f}<br>较年初: %{customdata}%<extra></extra>',
      },
      {
        x: dates, y: benchmarkAssets, mode: 'lines', name: '沪深300基准',
        line: { color: '#95a5a6', width: 1.5, dash: 'dash' },
        customdata: benchmarkYtdReturns.map(r => r.toFixed(2)),
        hovertemplate: '%{x|%Y-%m-%d}<br>基准: ¥%{y:,.0f}<br>较年初: %{customdata}%<extra></extra>',
      },
    ];

    const layout = {
      title: { text: `方案${currentScheme} 收益曲线（30万本金 Top3）`, font: { size: 14 } },
      xaxis: { title: '日期', tickformat: '%Y-%m-%d' },
      yaxis: { title: '账户净值（元）', rangemode: 'tozero' },
      hovermode: 'x unified',
      template: 'plotly_white',
      legend: { orientation: 'h', y: 1.06, x: 1, xanchor: 'right' },
      margin: { l: 60, r: 30, t: 50, b: 40 },
      height: 420,
    };

    Plotly.newPlot('chart-equity', traces, layout, { responsive: true });
  } catch (e) {
    console.error('loadEquityChart error:', e);
  }
}

// ========== 区域5：排行榜 ==========
async function loadRanking() {
  try {
    const session = document.getElementById('session-select').value;
    const resp = await fetch(`/api/ranking?scheme=${currentScheme}&session=${session}`);
    const data = await resp.json();
    const scores = data.scores || [];
    const actionCodes = new Set(data.action_codes || []);
    const date = data.date;

    document.getElementById('ranking-date').textContent = date ? `(${date})` : '';

    const tbody = document.getElementById('ranking-body');
    tbody.innerHTML = '';

    scores.forEach((s, i) => {
      const rank = s[`rank_${currentScheme.toLowerCase()}`] || (i + 1);
      const score = s[`score_${currentScheme.toLowerCase()}`] || 0;
      const actionRank = (data.action_codes || []).indexOf(s.code) + 1;
      const rankClass = actionRank > 0 ? `rank-${actionRank}` : '';
      const maClass = s.above_ma60 ? 'above-ma' : 'below-ma';

      // 持仓状态
      const code = s.code;
      let statusHtml = '';
      if (currentSellCodes.has(code)) {
        statusHtml = '<span class="status-badge status-sell">🔴 本周卖出</span>';
      } else if (currentHoldingCodes.has(code)) {
        statusHtml = '<span class="status-badge status-hold">🟢 继续持有</span>';
      } else if (currentNewEntryCodes.has(code)) {
        statusHtml = '<span class="status-badge status-new">🟡 新进入</span>';
      }

      // 只有板块去重后的 Top3 才是实际操作标的。
      const actionBadge = actionCodes.has(code) ? `<span class="badge bg-primary ms-1" style="font-size:0.6rem">操作${actionRank}</span>` : '';

      const tr = document.createElement('tr');
      tr.className = rankClass;
      tr.innerHTML = `
        <td class="rank-num">${rank}${actionBadge}</td>
        <td>${s.code}</td>
        <td>${s.name || s.code}</td>
        <td>${s.sector || ''}</td>
        <td>${s.gain_20d != null ? s.gain_20d + '%' : '--'}</td>
        <td>${s.up_days_20d != null ? s.up_days_20d : '--'}</td>
        <td><strong>${score}</strong></td>
        <td class="${maClass}">${s.above_ma60 ? '上方' : '下方'}</td>
        <td>${statusHtml}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('loadRanking error:', e);
  }
}

// ========== 区域3：当前持仓 + 换仓信息 ==========
async function loadCurrentHoldings() {
  try {
    const resp = await fetch(`/api/trade/current?scheme=${currentScheme}`);
    const data = await resp.json();

    currentHoldingCodes = new Set();
    currentSellCodes = new Set();
    const holdings = data.holdings || [];
    const newEntries = data.new_entries || [];
    const rebalance = data.next_rebalance || {};

    // === 上半部分：当前持仓 ===
    const container = document.getElementById('current-holdings');
    container.innerHTML = '';

    const sellItems = [];
    const holdItems = [];

    holdings.forEach(h => {
      const status = h.status || 'hold';
      const statusLabel = status === 'hold' ? '🟢 继续持有' : '🔴 本周卖出';
      const statusClass = status === 'hold' ? 'status-hold' : 'status-sell';
      const item = { ...h, status, statusLabel, statusClass, isNew: false };
      if (status === 'sell') {
        currentSellCodes.add(h.etf_code);
        sellItems.push(item);
      } else {
        currentHoldingCodes.add(h.etf_code);
        holdItems.push(item);
      }
    });

    if (holdItems.length === 0 && sellItems.length === 0) {
      container.innerHTML = '<div class="text-muted small">尚未开始交易</div>';
    } else {
      // 先显示继续持有，再显示本周卖出
      [...holdItems, ...sellItems].forEach(item => {
        const div = document.createElement('div');
        div.className = 'holding-item';
        const priceDisplay = item.amount ? '¥' + formatNum(item.amount) : '';
        let pnlHtml = '';
        if (item.pnl_pct != null && item.pnl_pct !== 0) {
          const pnlClass = item.pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative';
          const pnlSign = item.pnl_pct >= 0 ? '+' : '';
          pnlHtml = `<span class="${pnlClass}" style="font-size:0.85rem;margin-left:8px">${pnlSign}${item.pnl_pct}%</span>`;
        }
        div.innerHTML = `
          <div>
            <div class="name">${item.etf_name || item.etf_code} <small class="text-muted">${item.etf_code}</small>${pnlHtml}</div>
            <div class="detail">${item.shares > 0 ? item.shares.toLocaleString('zh-CN') + '份 · 市值' : ''} ${priceDisplay}</div>
          </div>
          <span class="status-badge ${item.statusClass}">${item.statusLabel}</span>
        `;
        container.appendChild(div);
      });
    }

    // === 下半部分：下周持仓 ===
    const nextContainer = document.getElementById('next-week-holdings');
    nextContainer.innerHTML = '';

    // 下周持仓 = 继续持有的 + 新进入的
    const nextHoldItems = [];
    holdItems.forEach(h => {
      nextHoldItems.push({ ...h, statusLabel: '🟢 保留', statusClass: 'status-hold' });
    });
    newEntries.forEach(n => {
      currentNewEntryCodes.add(n.code);
      nextHoldItems.push({
        etf_code: n.code,
        etf_name: n.name,
        price: 0,
        shares: 0,
        amount: 0,
        status: 'new',
        statusLabel: '🟡 新进入',
        statusClass: 'status-new',
        isNew: true,
      });
    });

    if (nextHoldItems.length === 0) {
      nextContainer.innerHTML = '<div class="text-muted small">暂无数据</div>';
    } else {
      nextHoldItems.forEach(item => {
        const div = document.createElement('div');
        div.className = 'holding-item';
        const priceDisplay = item.isNew ? '待买入' : '¥' + formatNum(item.amount || 0);
        div.innerHTML = `
          <div>
            <div class="name">${item.etf_name || item.etf_code} <small class="text-muted">${item.etf_code}</small></div>
            <div class="detail">${!item.isNew && item.shares > 0 ? item.shares.toLocaleString('zh-CN') + '份 · 市值' : ''} ${priceDisplay}</div>
          </div>
          <span class="status-badge ${item.statusClass}">${item.statusLabel}</span>
        `;
        nextContainer.appendChild(div);
      });
    }

    // 下次换仓日期
    document.getElementById('rb-next-date').textContent =
      rebalance.date ? `${rebalance.date}（${rebalance.weekday}，还有${rebalance.days_away}天）` : '--';

    // 刷新排行榜以更新持仓状态
    loadRanking();
  } catch (e) {
    console.error('loadCurrentHoldings error:', e);
  }
}

// ========== 区域6左：换仓历史时间轴 ==========
let historyOffset = 0;
async function loadTradeHistory() {
  try {
    const resp = await fetch(`/api/trade/history?scheme=${currentScheme}&limit=100`);
    const data = await resp.json();
    allTradeHistory = data.history || [];
    historyShown = 12;
    renderTradeTimeline();

    const btn = document.getElementById('btn-more-history');
    btn.style.display = allTradeHistory.length > 12 ? '' : 'none';
  } catch (e) {
    console.error('loadTradeHistory error:', e);
  }
}

function renderTradeTimeline() {
  const container = document.getElementById('trade-timeline');
  container.innerHTML = '';

  const items = allTradeHistory.slice(0, historyShown);

  if (items.length === 0) {
    container.innerHTML = '<div class="text-muted small">暂无换仓记录</div>';
    return;
  }

  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'timeline-item';

    const buys = item.buys.map(b => `${b.name}(+¥${formatNum(b.amount)})`).join('、');
    const sells = item.sells.map(s => s.name).join('、');
    const holds = item.holds.map(h => h.name).join('、');

    let detail = '';
    if (buys) detail += `买入: ${buys} `;
    if (sells) detail += `卖出: ${sells} `;
    if (holds) detail += `保留: ${holds}`;

    const assetStr = item.total_asset ? `账户: ¥${formatNum(item.total_asset)}` : '';

    div.innerHTML = `
      <div class="date">${item.date}</div>
      <div class="detail">${detail || '无变动'}</div>
      <div class="asset">${assetStr}</div>
    `;
    container.appendChild(div);
  });

  const btn = document.getElementById('btn-more-history');
  btn.style.display = historyShown < allTradeHistory.length ? '' : 'none';
}

// ========== 区域6右：策略统计 ==========
async function loadStrategyStats() {
  try {
    const resp = await fetch(`/api/trade/history?scheme=${currentScheme}&limit=100`);
    const data = await resp.json();
    const history = data.history || [];

    // 平均每次换手只数（最近12周）
    const recent = history.slice(0, 12);
    let totalTurnover = 0;
    let rebalanceCount = 0;
    recent.forEach(h => {
      const changes = h.buys.length + h.sells.length;
      if (changes > 0) {
        totalTurnover += changes;
        rebalanceCount++;
      }
    });
    const avgTurnover = rebalanceCount > 0 ? (totalTurnover / rebalanceCount).toFixed(1) : '--';
    document.getElementById('st-avg-turnover').textContent = avgTurnover + ' 只';

    // 信号稳定性：最近4周Top3重叠度
    const overlap = calculateTop3Overlap(history.slice(0, 4));
    document.getElementById('st-stability').textContent = overlap + '%';

    // 持仓最长连续持有
    const maxHold = calculateMaxConsecutiveHold(history);
    document.getElementById('st-max-hold').textContent = maxHold;

    // 本年换仓总次数
    document.getElementById('st-total-rebalance').textContent = history.filter(h => h.buys.length > 0 || h.sells.length > 0).length + ' 次';
  } catch (e) {
    console.error('loadStrategyStats error:', e);
  }
}

function calculateTop3Overlap(recentWeeks) {
  if (recentWeeks.length < 2) return '--';
  let totalOverlap = 0;
  let pairs = 0;
  for (let i = 0; i < recentWeeks.length - 1; i++) {
    const codes1 = new Set([
      ...recentWeeks[i].buys.map(b => b.code),
      ...recentWeeks[i].holds.map(h => h.code),
    ]);
    const codes2 = new Set([
      ...recentWeeks[i + 1].buys.map(b => b.code),
      ...recentWeeks[i + 1].holds.map(h => h.code),
    ]);
    if (codes1.size > 0 && codes2.size > 0) {
      let overlap = 0;
      codes1.forEach(c => { if (codes2.has(c)) overlap++; });
      totalOverlap += overlap / Math.min(codes1.size, codes2.size);
      pairs++;
    }
  }
  return pairs > 0 ? Math.round(totalOverlap / pairs * 100) : '--';
}

function calculateMaxConsecutiveHold(history) {
  if (history.length === 0) return '--';
  const codeWeeks = {};
  // 倒序（history是倒序的），转为正序
  const sorted = [...history].reverse();
  sorted.forEach((h, idx) => {
    const codes = [
      ...h.holds.map(x => x.code),
      ...h.buys.map(x => x.code),
    ];
    codes.forEach(code => {
      if (!codeWeeks[code]) codeWeeks[code] = [];
      codeWeeks[code].push(idx);
    });
  });

  let maxLen = 0;
  let maxCode = '';
  for (const [code, weeks] of Object.entries(codeWeeks)) {
    weeks.sort((a, b) => a - b);
    let streak = 1;
    let bestStreak = 1;
    for (let i = 1; i < weeks.length; i++) {
      if (weeks[i] === weeks[i - 1] + 1) {
        streak++;
        if (streak > bestStreak) bestStreak = streak;
      } else {
        streak = 1;
      }
    }
    if (bestStreak > maxLen) {
      maxLen = bestStreak;
      maxCode = code;
    }
  }
  return maxLen > 0 ? `${maxLen}周` : '--';
}

// ========== 手动刷新（异步轮询）==========
async function triggerRefresh() {
  const btn = document.getElementById('btn-refresh');
  btn.disabled = true;
  btn.textContent = '⏳ 刷新中...';

  try {
    const resp = await fetch('/api/refresh', { method: 'POST' });
    const data = await resp.json();

    if (data.status === 'started' || data.status === 'busy') {
      // 后台更新已启动，每5秒轮询一次状态
      pollRefreshStatus(btn);
    } else {
      // skip / error
      btn.textContent = '⚠ ' + (data.message || '跳过');
      btn.disabled = false;
      setTimeout(() => { btn.textContent = '🔄 刷新'; }, 4000);
    }
  } catch (e) {
    btn.textContent = '❌ 刷新失败';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = '🔄 刷新'; }, 3000);
  }
}

async function pollRefreshStatus(btn) {
  try {
    const resp = await fetch('/api/refresh/status');
    const data = await resp.json();
    if (data.running) {
      setTimeout(() => pollRefreshStatus(btn), 5000);
    } else {
      btn.textContent = '✅ 已刷新';
      btn.disabled = false;
      setTimeout(loadAll, 300);
      setTimeout(() => { btn.textContent = '🔄 刷新'; }, 3000);
    }
  } catch (e) {
    // 网络抖动，继续重试
    setTimeout(() => pollRefreshStatus(btn), 5000);
  }
}

// ========== CSV导出 ==========
function exportCSV() {
  const table = document.getElementById('ranking-table');
  const rows = table.querySelectorAll('tr');
  let csv = '';
  rows.forEach(row => {
    const cells = row.querySelectorAll('th, td');
    const line = Array.from(cells).map(c => c.textContent.trim().replace(/,/g, ' ')).join(',');
    csv += line + '\n';
  });
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `etf_ranking_${currentScheme}_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ========== 工具函数 ==========
function formatNum(n) {
  if (n == null) return '--';
  return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}
