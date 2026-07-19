# AiToEarn — ETF 板块轮动实时仪表盘

## 当前状态
- **前复权+风控+现金追踪修复完成**（2026-07-19），回测结果恢复正常
- **全量数据回填完成**（2026-07-16），99/100 只 ETF 成功，2020-01-01 ~ 2026-07-16
- **三级数据源降级策略**：akshare（主）→ efinance.fund（降级）→ baostock（最终备选）
- SQLite 数据库已重建，评分历史/回测净值/换仓记录均已更新
- Flask 应用运行中 http://127.0.0.1:5001/
- 99 只 ETF 参与评分，V1/V2 回测已完成

## 全量回填结果（2026-07-16）
- 拉取成功率：99/100（99%），仅 560730 N红利低波ETF国泰海通 失败
- 数据源分布：akshare 7只，efinance 92只（efinance 承担了主要降级角色）
- 日期范围：2020-01-01 ~ 2026-07-16

### V1 回测（Top8 等权，2020-01 ~ 2026-07，前复权数据）
| 方案 | 累计收益 | 年化收益 | 夏普 | 最大回撤 |
|------|---------|---------|------|---------|
| A 激进 | +333.38% | 25.15% | 0.93 | -24.37% |
| B 平衡 | +361.44% | 26.36% | 0.98 | -24.76% |
| C 稳健 | +385.45% | 27.35% | 1.03 | -22.80% |

### V2 回测（30万本金 Top3，2026年至今，前复权+风控+现金追踪修复）
| 方案 | 总资产 | 累计收益 | 夏普 | 最大回撤 |
|------|--------|----------|------|----------|
| A 激进 | ¥438,247 | +46.08% | 1.79 | -15.58% |
| B 平衡 | ¥417,903 | +39.30% | 1.60 | -17.46% |
| C 稳健 | ¥319,102 | +6.37% | 0.40 | -17.82% |

## 项目结构
```
AiToEarn/
├── app.py                      # Flask 启动入口（支持 HOST/PORT 环境变量覆盖）
├── config.py                   # 全局配置
├── daily_update.py             # 每日增量更新脚本（V1+V2回测）
├── Dockerfile                  # Docker 镜像定义（Python 3.9-slim）
├── docker-compose.yml          # Docker Compose 编排（volume 持久化）
├── requirements.txt            # Python 依赖
├── 策略.md                     # 策略详细说明
├── ETF热点板块轮动.docx         # 项目原始需求文档
│
├── app/                        # Flask Web 应用
│   ├── __init__.py             # Flask 工厂，注册蓝图
│   ├── routes/
│   │   ├── dashboard.py        # 仪表盘首页路由 /
│   │   ├── ranking.py          # ETF 排行 API  /api/ranking
│   │   ├── equity.py           # 收益曲线 API  /api/equity/chart, /api/equity/v2
│   │   ├── trade_history.py    # 换仓历史 API  /api/trade/history, /api/trade/current
│   │   └── refresh.py          # 手动刷新 API  /api/refresh
│   ├── templates/
│   │   └── dashboard.html      # 仪表盘主页（排行→操作指令→曲线→历史/统计→总览）
│   └── static/
│       ├── css/style.css       # 样式（操作指令卡片/时间轴/排行高亮等）
│       └── js/
│           └── dashboard.js    # 方案联动刷新/6区域数据加载
│
├── core/                       # 核心计算逻辑
│   ├── adjust.py               # 前复权处理（检测除权跳变，累积调整因子）
│   ├── data_fetcher.py         # 三级数据源降级获取（akshare→efinance→baostock）+ 加载时前复权
│   ├── indicators.py           # 评分计算 + 乖离率 + Top N 板块去重
│   ├── backtest.py             # V1回测(Top8等权) + V2回测(30万本金Top3, 含风控)
│   ├── risk_control.py         # 风控模块（分层止损 + 乖离率惩罚）
│   ├── validator.py            # 数据质量校验
│   └── scheduler.py            # 定时调度（schedule 库，14:30 + 15:30）
│
├── db/                         # SQLite 数据库
│   ├── schema.sql              # 9 张表
│   ├── models.py               # 8 个 Model 类
│   └── aitorearn.db            # SQLite 数据文件
│
└── data/                       # ETF K线 CSV 缓存（99只）
```

## 回测策略说明

### 评分因子（三因子模型）
- 因子1：20日涨跌幅(w1) — A:0.7, B:0.6, C:0.5
- 因子2：20日上涨天数(w2) — A:0.3, B:0.4, C:0.5
- 因子3：20日乖离率惩罚(w3) — A:0.15, B:0.3, C:0.5
  - deviation_20d = (close - MA20) / MA20 × 100
  - score_final = score_original - w3 × max(0, deviation - median)
  - 仅对乖离率高于中位数的ETF施加惩罚
- 板块去重取 Top 8，等权持仓，基准沪深300

### V2 策略（新增，含风控）
- 初始本金：300,000 元
- 选股：评分前3名（板块去重保留），含乖离率评分惩罚
- 仓位：账户总资产 ÷ 3（等权，随净值滚动）
- 换仓频率：每周最后一个交易日收盘价执行
- 交易成本：0
- 基准：30万买入沪深300（510300）持有不动
- 回测起始：本年度第一个交易日
- **个股层止损**：连续2日收盘价低于 MA5×(1-1.5%) → 减仓至50%
- **组合层止损**：净值回撤超过8% → 全部清仓，保守再入场

## Web 仪表盘功能
| 功能 | 说明 | 状态 |
|------|------|------|
| ETF 评分排行 | 全部99条，前3金银铜高亮，持仓状态 | ✅ 已验证 |
| 本周操作指令 | 当前持仓+下周持仓（左右两栏） | ✅ 已验证 |
| 收益曲线 | Plotly交互图表，策略vs基准，hover含较年初收益率 | ✅ 已验证 |
| 换仓历史时间轴 | 按周倒序，查看更多展开 | ✅ 已验证 |
| 策略统计指标 | 换手率/信号稳定性/连续持有/换仓次数 | ✅ 已验证 |
| 账户总览卡片 | 总资产/累计收益/最大回撤/夏普（页面底部） | ✅ 已验证 |
| 手动刷新 | 判断交易日+交易时段 | ✅ 已实现 |
| 排行 CSV 导出 | 下载当前排行数据 | ✅ 已实现 |

## 数据流
```
定时调度 (14:30/15:30) 或 手动刷新
  → daily_update.py
    → core/data_fetcher.py 增量拉取（akshare→efinance→baostock 三级降级）
    → core/validator.py 质量校验
    → core/indicators.py 评分计算 → db/score_history
    → core/backtest.py V1回测 → db/equity_curve
    → core/backtest.py V2回测 → db/equity_curve_v2 + db/trade_history
    → Flask 前端读取 API 展示
```

## API 接口
| 路径 | 说明 | 参数 |
|------|------|------|
| /api/equity/v2 | V2收益曲线+统计 | scheme, range(ytd/3m/all) |
| /api/trade/history | 换仓历史（按日期分组） | scheme, limit |
| /api/trade/current | 当前持仓+换仓信息 | scheme |
| /api/ranking | ETF排行 | scheme, session |
| /api/equity/chart | V1 Plotly图表 | - |
| /api/refresh | 手动刷新 | POST |

## 关键决策记录
1. **数据源**: akshare（主）→ efinance.fund（降级）→ baostock（最终备选），三级降级策略
2. **增量更新**: 检查缓存最后日期，仅拉取增量部分
3. **调度方案**: schedule 库在 Flask 线程中运行
4. **数据准确性**: efinance.fund 返回 NAV 数据，使用 close=单位净值，open=close.shift(1) 近似 OHLC
5. **V2回测设计**: 30万初始本金，Top3等权，每周换仓，换仓日为当周最后一个交易日（节假日顺延）
6. **MA60筛选宽松化**: V2回测中，MA60不可用（数据不足）的ETF不跳过，仅当MA60可用且价格低于MA60时才过滤
7. **V2换仓逻辑**: 先卖后买，重叠ETF调整数量差，非重叠完整买卖
8. **前端6区域布局**: 排行→操作指令→曲线→历史+统计→账户总览（自上而下），方案切换联动全页
9. **操作指令面板**: 左右两栏布局，当前持仓在左，下周持仓在右
10. **ETF名称映射**: get_etf_pool() 返回 (pool, full_name_map) 覆盖全市场ETF
11. **交易日历类型**: akshare返回datetime.date→统一转字符串
12. **Docker部署**: Flask 需监听 0.0.0.0（非127.0.0.1），通过 HOST 环境变量覆盖；数据目录(db/data)通过 volume 持久化
13. **排行全量展示**: API 默认返回全部评分数据，前端不传 limit 参数
14. **收益曲线**: 显示策略净值+基准净值两条线，hover显示较年初收益率，已去掉超额收益线
15. **风控模块集成**: core/risk_control.py 实现分层止损（个股MA5 + 组合回撤）和乖离率评分惩罚；V2回测集成每日风控检查；daily_update增加风控步骤5
16. **数据库新增**: score_history 增加6个字段（deviation_20d/median/penalty + score_final_a/b/c）；新增 risk_control_log 表
17. **三因子评分**: WEIGHTS 增加 w3（乖离率权重），config.py 新增 DEVIATION_WEIGHTS 和风控参数；score_final = score_original - w3 × max(0, dev - median)
18. **前复权处理**: 新增 core/adjust.py，检测除权跳变（单日跌幅>-20%），累积调整因子做前复权；load_cached_data() 加载时自动应用；不改原始 CSV
19. **V2 现金追踪修复**: total_asset = market_value + cash，减仓/清仓的卖出金额正确计入现金
20. **风控 triggered 标志修复**: PortfolioTrailingStop.reset_period() 同时重置 triggered=False 和 trigger_date=None，允许换仓后重新触发
21. **换仓日风控检查**: rebalance day 分支开头增加风控检查，先检查止损再执行新交易
22. **端口 5001**: macOS AirPlay 占用 5000，统一改为 5001（config.py/Dockerfile/docker-compose.yml）

## 技术依赖（已全部安装）
- Python 3.9.6+
- Flask 3.1.3 ✅
- schedule 1.2.2 ✅
- Plotly 6.8.0 ✅
- baostock ✅
- akshare 1.18.60 ✅
- efinance ✅
- pandas 2.3.3 ✅
- SQLite（Python 内置）

## 数据库表
| 表名 | 用途 |
|------|------|
| score_history | 评分历史（每日，含乖离率和最终评分） |
| equity_curve | V1回测净值 |
| equity_curve_v2 | V2回测净值（30万本金） |
| trade_history | V2换仓记录（buy/sell/hold） |
| risk_control_log | 风控事件日志（个股层/组合层） |
| data_quality_log | 数据质量日志 |
| trades | 个人交易（已弃用，表保留） |
| holdings | 个人持仓（已弃用，表保留） |
| daily_pnl | 每日盈亏（已弃用，表保留） |

## 下一步待做
1. **Docker 部署到 Mac mini**: 使用项目根目录的 Dockerfile + docker-compose.yml
2. **增量更新到最新交易日**: 工作日运行 `python daily_update.py`
3. **前端展示风控事件**: 仪表盘增加风控日志展示区域

## 已知问题
- 每次数据更新约 4-6 只 ETF 网络请求失败（akshare API 不稳定，失败 ETF 不会进 Top 3）
- baostock 交叉校验 9/10 通过，失败的具体 ETF 和偏差值已加入打印（daily_update.py）

## 会话历史
- 2026-06-18: 项目初始化，完成 ETF 板块轮动策略首次回测(34只ETF)
- 2026-06-22: 基于策略分析板块热度，生成 HTML 报告
- 2026-06-22: 补爬 ETF 数据至 124 只，修正回测偏差
- 2026-06-24: Web 仪表盘搭建 — Flask Web 应用、模块化重构、SQLite、双数据源
- 2026-06-25: 验证与修复 — 安装依赖、修复bug、首次数据更新、Flask验证
- 2026-06-25: ETF名称修复 + 回测一致性验证 + 移除个人持仓模块
- 2026-06-25: **V2回测引擎 + 前端改版** — 新增run_backtest_v2(30万本金Top3)、新增equity_curve_v2/trade_history表、新增4个API接口、前端6区域布局改版、方案切换联动刷新
- 2026-06-25: **补全遗漏** — 持仓卡片追加浮盈浮亏%、换仓历史时间轴追加当日账户总资产、baostock交叉校验失败详情打印
- 2026-06-25: **修复收益曲线起始+换仓日期** — 移除MA60整体延迟起始日逻辑，回测从1月起；修复换仓日期逻辑；收益曲线横轴日期格式yyyy-MM-dd
- 2026-07-03: **前端三项改动** — 布局调整：账户总览移到底部；排行展示全部评分数据；收益曲线hover增加每日收益率
- 2026-07-03: **Docker 部署支持** — 新增 Dockerfile + docker-compose.yml + .dockerignore；app.py 支持 HOST/PORT 环境变量覆盖
- 2026-07-16: **数据源升级** — 新增 efinance 作为第三数据源，实现三级降级（akshare→efinance→baostock）；全量回填 2020-01-01 ~ 2026-07-16，成功率 99%
- 2026-07-16: **前端三项修复** — 1)ETF排行本周卖出标记修复；2)操作指令面板改为左右两栏（当前持仓/下周持仓）；3)收益曲线改为较年初收益率，去掉超额收益线
- 2026-07-16: **项目清理** — 删除原始脚本(etf_rotation_backtest.py/fetch_missing.py/run_update.sh)、旧回测结果(results/)、一次性回填脚本(backfill_data.py/backfill_full.py)、浏览器调试文件(.playwright-mcp/)、截图文件；保留策略.md和需求文档.docx
- 2026-07-17: **风控模块集成** — 新增 core/risk_control.py（分层止损+乖离率惩罚）；config.py 增加风控参数和DEVIATION_WEIGHTS；indicators.py 集成三因子评分；backtest.py V2回测集成每日风控检查；db 新增 risk_control_log 表 + score_history 6个新字段；daily_update 增加风控步骤
- 2026-07-19: **回测数据修复** — 1)新增 core/adjust.py 前复权模块（24只ETF共27处除权跳变全部消除）；2)修复V2回测现金追踪bug（减仓/清仓现金丢失）；3)修复风控triggered标志不重置bug；4)修复换仓日跳过风控检查bug；5)端口5000→5001；V1回测收益从~126%升至~361%，V2回测从-11%升至+39%（方案B）