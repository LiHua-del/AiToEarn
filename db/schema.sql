-- AiToEarn SQLite 数据库建表语句

-- 个人交易记录
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    etf_code TEXT NOT NULL,          -- ETF 代码
    etf_name TEXT DEFAULT '',        -- ETF 名称
    direction TEXT NOT NULL,         -- 'buy' | 'sell'
    quantity REAL NOT NULL,          -- 份数
    price REAL NOT NULL,             -- 成交价
    amount REAL NOT NULL,            -- 总金额
    trade_date TEXT NOT NULL,        -- 交易日期 YYYY-MM-DD
    notes TEXT DEFAULT '',           -- 备注
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 当前持仓快照（每次操作后重新计算）
CREATE TABLE IF NOT EXISTS holdings (
    etf_code TEXT PRIMARY KEY,
    etf_name TEXT DEFAULT '',
    total_quantity REAL NOT NULL DEFAULT 0,  -- 持有份数
    avg_cost REAL NOT NULL DEFAULT 0,        -- 平均成本价
    total_cost REAL NOT NULL DEFAULT 0,      -- 总成本
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 每日净值快照（用于个人收益曲线）
CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT PRIMARY KEY,            -- YYYY-MM-DD
    total_value REAL DEFAULT 0,       -- 总市值
    total_cost REAL DEFAULT 0,        -- 总成本
    pnl REAL DEFAULT 0,              -- 浮动盈亏
    pnl_pct REAL DEFAULT 0           -- 盈亏百分比
);

-- 数据质量日志
CREATE TABLE IF NOT EXISTS data_quality_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT DEFAULT '',           -- 'akshare' | 'baostock'
    session TEXT DEFAULT 'final',     -- 'intraday' | 'final'
    total_etfs INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    validation_errors TEXT DEFAULT '[]',  -- JSON 数组
    cross_check_passed INTEGER DEFAULT 1  -- 0|1
);

-- 评分历史（每次计算后保存）
CREATE TABLE IF NOT EXISTS score_history (
    date TEXT NOT NULL,
    session TEXT NOT NULL,            -- 'intraday' | 'final'
    code TEXT NOT NULL,
    name TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    gain_20d REAL DEFAULT 0,
    up_days_20d INTEGER DEFAULT 0,
    score_a REAL DEFAULT 0,           -- 方案A评分
    score_b REAL DEFAULT 0,           -- 方案B评分
    score_c REAL DEFAULT 0,           -- 方案C评分
    above_ma60 INTEGER DEFAULT 0,     -- 0|1
    rank_a INTEGER DEFAULT 0,
    rank_b INTEGER DEFAULT 0,
    rank_c INTEGER DEFAULT 0,
    PRIMARY KEY (date, session, code)
);

-- 回测净值序列（用于前端收益曲线，JSON 文件替代，此表备用）
CREATE TABLE IF NOT EXISTS equity_curve (
    date TEXT NOT NULL,
    scheme TEXT NOT NULL,             -- 'A' | 'B' | 'C' | 'benchmark'
    nav REAL DEFAULT 1.0,             -- 净值
    PRIMARY KEY (date, scheme)
);

-- V2 回测净值序列（30万本金 Top3 滚动）
CREATE TABLE IF NOT EXISTS equity_curve_v2 (
    date TEXT NOT NULL,
    scheme TEXT NOT NULL,             -- 'A' | 'B' | 'C'
    total_asset REAL DEFAULT 300000,  -- 账户总资产（元）
    benchmark_asset REAL DEFAULT 300000,  -- 基准净值（元）
    PRIMARY KEY (date, scheme)
);

-- V2 回测换仓历史
CREATE TABLE IF NOT EXISTS trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,               -- 换仓日期 YYYY-MM-DD
    action TEXT NOT NULL,             -- 'buy' | 'sell' | 'hold'
    etf_code TEXT NOT NULL,           -- ETF 代码
    etf_name TEXT DEFAULT '',         -- ETF 名称
    price REAL DEFAULT 0,             -- 成交价
    shares REAL DEFAULT 0,            -- 份额
    amount REAL DEFAULT 0,            -- 金额
    scheme TEXT NOT NULL DEFAULT 'B'  -- 'A' | 'B' | 'C'
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(etf_code);
CREATE INDEX IF NOT EXISTS idx_score_date ON score_history(date, session);
CREATE INDEX IF NOT EXISTS idx_equity_date ON equity_curve(date);
CREATE INDEX IF NOT EXISTS idx_equity_v2_date ON equity_curve_v2(date);
CREATE INDEX IF NOT EXISTS idx_trade_history_date ON trade_history(date, scheme);