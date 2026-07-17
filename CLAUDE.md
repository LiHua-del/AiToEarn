# AiToEarn — ETF 板块轮动实时仪表盘

## 当前状态
- **全量数据回填完成**（2026-07-16），99/100 只 ETF 成功，2020-01-01 ~ 2026-07-16
- **三级数据源降级策略**：akshare（主）→ efinance.fund（降级）→ baostock（最终备选）
- SQLite 数据库已重建，评分历史/回测净值/换仓记录均已更新
- Flask 应用运行中 http://127.0.0.1:5000/
- 99 只 ETF 参与评分，V1/V2 回测已完成

## 全量回填结果（2026-07-16）
- 拉取成功率：99/100（99%），仅 560730 N红利低波ETF国泰海通 失败
- 数据源分布：akshare 7只，efinance 92只（efinance 承担了主要降级角色）
- 日期范围：2020-01-01 ~ 2026-07-16

### V1 回测（Top8 等权）
| 方案 | 累计收益 | 年化收益 | 夏普 | 最大回撤 |
|------|---------|---------|------|---------|
| A 激进 | +125.54% | 13.25% | 0.49 | -35.89% |
| B 平衡 | +129.72% | 13.57% | 0.50 | -36.91% |
| C 稳健 | +126.98% | 13.36% | 0.50 | -37.05% |

### V2 回测（30万本金 Top3，2026年至今）
| 方案 | 总资产 | 累计收益 | 夏普 | 最大回撤 |
|------|--------|----------|------|----------|
| A 激进 | ¥267,636 | -10.79% | -0.12 | -46.13% |
| B 平衡 | ¥266,900 | -11.03% | -0.13 | -46.13% |
| C 稳健 | ¥269,501 | -10.17% | -0.10 | -46.13% |

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
│   ├── data_fetcher.py         # 三级数据源降级获取（akshare→efinance→baostock）
│   ├── indicators.py           # 评分计算 + Top N 板块去重
│   ├── backtest.py             # V1回测(Top8等权) + V2回测(30万本金Top3)
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

### V1 策略（原有，保留）
- 评分逻辑：20日涨跌幅(w1) + 20日上涨天数(w2)
- 三方案权重：A(0.7/0.3), B(0.6/0.4), C(0.5/0.5)
- 板块去重取 Top 8，等权持仓，基准沪深300

### V2 策略（新增）
- 初始本金：300,000 元
- 选股：评分前3名（板块去重保留）
- 仓位：账户总资产 ÷ 3（等权，随净值滚动）
- 换仓频率：每周最后一个交易日收盘价执行
- 交易成本：0
- 基准：30万买入沪深300（510300）持有不动
- 回测起始：本年度第一个交易日

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
| score_history | 评分历史（每日） |
| equity_curve | V1回测净值 |
| equity_curve_v2 | V2回测净值（30万本金） |
| trade_history | V2换仓记录（buy/sell/hold） |
| data_quality_log | 数据质量日志 |
| trades | 个人交易（已弃用，表保留） |
| holdings | 个人持仓（已弃用，表保留） |
| daily_pnl | 每日盈亏（已弃用，表保留） |

## 下一步待做
1. **Docker 部署到 Mac mini**: 使用项目根目录的 Dockerfile + docker-compose.yml
2. **增量更新到最新交易日**: 工作日运行 `python daily_update.py`

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