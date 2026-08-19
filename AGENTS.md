# AiToEarn — ETF 板块轮动实时仪表盘

## 当前状态
- **五年历史回测已完成**（2026-08-19）：独立行情覆盖 2021-01-01 ~ 2026-08-18，100/100 ETF 可用，原始缓存与日常库隔离
- **历史回测页面已上线**：新增累计复利曲线、逐年收益/回撤、数据质量和异常处理记录；整体布局调整为“实时决策→历史验证→评分排行→明细”
- **历史方案B最终结果**：30万元连续复利至 ¥445,000.56，累计 +48.33%，全期最大回撤 -43.25%，夏普 0.29
- **2026 年独立审计结果**：截至最近完整交易日 2026-08-18，方案B从30万元独立起算，期末 ¥305,323.24，收益 +1.77%，最大回撤 -24.89%，夏普 0.21
- **数据准确性处理**：新浪 ETF 交易所 OHLC + 累计分红；处理 110 次现金分红/份额折算，规则核验后保留 36 次真实极端行情
- **操作名单板块去重已修复**：宽基指数与对应增强ETF统一分类；排行榜“操作1/2/3”严格使用板块去重后的 Top3
- **跨境标的修复**：补全纳斯达克、道琼斯、中概等关键词，境内板块轮动池不再混入跨境 ETF
- **日常数据更新到 2026-08-18**，Flask 启动时会自动补当天数据
- **akshare `fund_etf_hist_em` 接口已失效**（ConnectionError），已做 fast-fail 处理快速降级
- **刷新异步化**：refresh.py 改为后台线程+轮询，不再阻塞 HTTP 请求
- **Flask 修复**：debug=False + use_reloader=False，避免文件写入时触发重启
- **启动自动检测**：Flask 启动时判断数据是否落后，落后则后台补充更新
- Flask 应用运行中 http://127.0.0.1:5001/
- V2 当前持仓（方案B，截至2026-08-18）：中证2000增强ETF招商、有色ETF汇添富、科创半导体ETF华夏

### V1 回测（Top8 等权，2020-01 ~ 2026-08，前复权数据）
| 方案 | 累计收益 | 年化收益 | 夏普 | 最大回撤 |
|------|---------|---------|------|---------|
| A 激进 | +322.81% | 24.42% | 0.88 | -26.20% |
| B 平衡 | +336.75% | 25.03% | 0.91 | -26.70% |
| C 稳健 | +346.59% | 25.45% | 0.93 | -26.78% |

### V2 回测（30万本金 Top3，2026-01 ~ 2026-08-07，前复权+风控）
| 方案 | 总资产 | 累计收益 | 夏普 | 最大回撤 |
|------|--------|----------|------|----------|
| A 激进 | ¥358,898 | +19.63% | 0.85 | -19.25% |
| B 平衡 | ¥356,561 | +18.85% | 0.82 | -19.21% |
| C 稳健 | ¥337,867 | +12.62% | 0.61 | -20.26% |

### 五年连续复利回测（2021-01-01 ~ 2026-08-18，单边成本0.05%）
| 方案 | 期末资产 | 累计收益 | 夏普 | 全期最大回撤 |
|------|----------|----------|------|--------------|
| A 激进 | ¥350,417.85 | +16.81% | 0.15 | -46.07% |
| B 平衡 | ¥445,000.56 | +48.33% | 0.29 | -43.25% |
| C 稳健 | ¥363,278.26 | +21.09% | 0.16 | -44.10% |

方案B逐年：2021 -12.49%、2022 -25.01%、2023 +8.03%、2024 +9.34%、2025 +58.51%、2026 连续复利口径 +20.73%。2026 独立30万元起算口径为 +1.77%。

## 项目结构
```
AiToEarn/
├── app.py                      # Flask 启动入口（支持 HOST/PORT 环境变量覆盖）
├── config.py                   # 全局配置
├── daily_update.py             # 每日增量更新脚本（V1+V2回测）
├── run_historical_backtest.py  # 独立五年行情审计+连续复利回测入口
├── Dockerfile                  # Docker 镜像定义（Python 3.9-slim）
├── docker-compose.yml          # Docker Compose 编排（volume 持久化）
├── requirements.txt            # Python 依赖
├── 策略.md                     # 策略详细说明
├── ETF热点板块轮动.docx         # 项目原始需求文档
│
├── app/                        # Flask Web 应用
│   ├── __init__.py             # Flask 工厂，注册蓝图
│   ├── routes/
│   │   ├── historical.py       # 五年历史回测 API /api/historical
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
│   ├── historical_backtest.py  # 新浪OHLC/分红、异常审计、五年复利引擎
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
23. **akshare hist fast-fail**: fund_etf_hist_em 接口失效，ConnectionError/ChunkedEncodingError 直接 re-raise，不重试，快速降级到 efinance
24. **efinance 增量最小行数修复**: _try_fetch_etf 对 efinance 的检查从 MIN_DATA_ROWS(65) 改为 >0，因为 efinance 下载全量历史再按日期过滤，增量更新只有几十行是正常的
25. **刷新异步化**: refresh.py 用后台线程执行 run_update()，立即返回 {status:'started'}；前端每 5s 轮询 /api/refresh/status；刷新完成后自动 loadAll()
26. **Flask 稳定化**: FLASK_DEBUG=False + use_reloader=False，避免 CSV 写入触发重启
27. **启动检测 bug 修复**: scheduler.py 的 ScoreHistoryModel.get_latest 返回 (list, date) 元组，需解包获取日期；调用需传 session='final', scheme='B' 关键字参数
28. **历史回测数据隔离**: `history_data/` 与日常 `data/`、SQLite 完全隔离；页面读取 `app/data/history_backtest.json`，不会覆盖当前持仓或一年期曲线
29. **历史行情口径**: 使用新浪 ETF 交易所 OHLC 和累计分红；现金分红按累计值差分，隔夜跳空超过30%且日内正常的事件按份额折算/量纲切换复权，复权后超过15%的行情保留并披露
30. **五年复利口径**: 2021年一次投入30万元，跨年度不重置；Top3板块去重、周末交易日尾盘、MA5+8%区间回撤风控；单边交易成本0.05%，目标仓位预留手续费以禁止负现金
31. **准确性边界**: 当前标的池回溯存在幸存者偏差；缺少历史每日规模/流动性快照，不能宣称为无偏的 point-in-time 回测，页面必须持续披露该限制
32. **境内池关键词**: 跨境过滤补充纳斯达克、道琼斯、中概、海外、全球、跨境等关键词，避免跨境ETF混入境内板块轮动
33. **宽基板块统一**: 中证A500/2000/1000/500、沪深300、上证50及对应增强产品统一归入各自指数板块，防止同一指数重复占用 Top3
34. **操作角标口径**: 排名列保留全量评分顺序；“操作1/2/3”和行高亮只使用服务端板块去重后的 `action_codes`，不能直接使用原始 `rank <= 3`
35. **年度审计口径**: “2026 当年”从300,000元独立起算，与五年连续复利结果分开；只使用全部标的共同具备的最近完整收盘日，盘中评分不混入历史审计

## 新设备部署步骤

> **注意**：`db/aitorearn.db`（数据库）和 `data/`（ETF K线CSV缓存）均在 `.gitignore` 中，
> 新设备 clone 后这两者为空，必须按以下步骤初始化。

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 全量初始化（首次必须执行，耗时约 15–25 分钟）
```bash
python3 -u daily_update.py 2>&1 | tee /tmp/init.log
```
- 自动创建 SQLite 数据库（从 `db/schema.sql`）
- 从 efinance 下载 100 只 ETF 的全量历史数据（2020 至今）到 `data/`
- 计算评分、V1/V2 回测，写入数据库
- 进度实时输出到终端和 `/tmp/init.log`
- 成功标志：最后一行出现 `更新完成!`，成功率显示 `100/100 (100.0%)`

### 2.1 生成五年历史回测（首次部署或需要刷新时执行）
```bash
python3 -u run_historical_backtest.py --refresh 2>&1 | tee /tmp/history_backtest.log
```
- 原始数据写入 `history_data/`（已 gitignore），页面结果写入 `app/data/history_backtest.json`
- 日常启动可直接使用仓库内已提交的结果；只有需要更新历史截止日时才必须重跑
- 成功标志：输出 `历史回测完成`、`可用 ETF: 100/100`
- 该脚本需要可访问新浪财经；失败时不要用 efinance 单位净值覆盖交易行情

### 3. 启动 Flask
```bash
python3 app.py
```
访问 http://127.0.0.1:5001/

### 注意事项
- **端口 5001**：macOS AirPlay 占用 5000，已改用 5001；如需改端口设 `PORT=xxxx` 环境变量
- **akshare hist 接口已失效**：程序已自动 fast-fail 降级到 efinance，无需手动处理
- **每日更新**：Flask 启动后调度器自动在 14:30（盘中）和 15:30（收盘后）触发；启动时若数据落后也会自动后台补充
- **手动刷新**：点页面右上角 🔄 刷新按钮（异步执行，完成后自动刷新页面）
- **Python 版本**：3.9+（项目在 3.9-slim 镜像中测试通过）

### Docker 部署（可选）
```bash
docker compose up -d
```
- 数据目录通过 volume 持久化，首次启动同样需要等待全量初始化完成
- 初始化日志：`docker compose logs -f`

## 下一步待做
1. **每日增量更新**: 工作日 15:30 定时调度自动运行，也可手动点刷新
2. **前端展示风控事件**: 仪表盘增加风控日志展示区域

## 已知问题
- akshare `fund_etf_hist_em` 接口失效（2026-08 起），已 fast-fail 降级到 efinance，不影响更新
- baostock 层目前也用 akshare 实现（等价于第二次尝试 akshare），实际无效；efinance 承担了所有降级工作
- 五年历史回测使用当前 ETF 池向过去回溯，存在幸存者偏差；历史每日规模/成交额准入尚未 point-in-time 化
- 8%组合止损按每周换仓区间重置峰值，不等同于全周期最大回撤限制，因此历史最大回撤仍可能显著超过8%

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
- 2026-08-07: **数据更新+刷新机制修复** — 1)akshare hist接口完全失效，fast-fail快速降级；2)efinance增量检查MIN_DATA_ROWS=65错误导致100%失败，修复为>0；3)refresh.py改为异步（后台线程+轮询）；4)Flask改debug=False+use_reloader=False；5)修复启动检测get_latest参数bug；全量更新至2026-08-07，100/100成功
- 2026-08-08: **板块分类修复** — config.py 将"有色""矿业"从煤炭板块独立为"有色金属"板块，"化工"独立为"化工"板块；补充新设备部署说明到AGENTS.md
- 2026-08-19: **五年历史回测+页面重构** — 新增新浪OHLC/分红独立数据管线、异常跳变审计、30万元跨年连续复利回测、`/api/historical` 与历史展示板块；补全跨境ETF过滤；页面调整为实时决策优先、历史验证独立展示（结果以后续板块去重重跑值为准）
- 2026-08-19: **操作板块去重+年度独立审计** — 统一宽基指数及增强ETF分类；排行操作角标改用去重Top3；100/100只ETF重跑含风控的2026年独立30万元回测；历史模块新增五年/当年切换；方案B当年¥305,323.24（+1.77%，最大回撤-24.89%）
