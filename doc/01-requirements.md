# FriesTrader 需求文档

## 1. 文档目的

本文档根据仓库中的 `README.md`、`PHASE_A_TASK.md`、`PHASE_B_TASK.md`、`risk_rules.json` 和 `scripts/` 反向整理系统需求。它描述当前仓库已经表达出来的需求，不代表额外承诺的产品能力。

## 2. 项目目标

FriesTrader 的目标是构建一个面向 Robinhood Agentic Trading MCP 的自动化股票交易流程：

- 由 LLM 在收盘后筛选候选股票、检索新闻并形成结构化交易 thesis；
- 由次日开盘后的第二阶段重新验证行情，并执行机械化风险控制；
- 只有通过所有硬性规则的候选才允许进入订单审核；
- 支持从 `dry_run` 逐步过渡到人工授权的 `live` 模式；
- 对每个筛选、风控、订单和周期结果进行可追溯记录。

系统不承诺盈利，不替代投资、税务或法律建议，也不提供经过验证的 alpha。

## 3. 范围

### 3.1 包含范围

- Robinhood watchlist、市场扫描、基本面、行情、历史价格、持仓和订单数据的读取；
- 候选筛选、信号判断和新闻研究；
- `long`、`avoid`、`exit_existing` 三类 thesis 方向；
- conviction 分级：`high`、`medium`、`low`；
- 止损、分级止盈、conviction trim、每日/每周亏损限制；
- wash-sale buy guard、卖出后重新进入锁定、财报 blackout；
- 新建仓位与已有持仓 top-up 的排序及仓位计算；
- dry-run 和 gated live order 流程；
- JSONL 审计日志与每日 Markdown 摘要；
- 通过 Git 仓库保存跨运行状态。

### 3.2 不包含范围

- 独立的交易所或券商连接器实现；
- 回测、仿真撮合和策略收益证明；
- 保证成交价格、止损价格或订单最终状态；
- 自动税务申报或完整 wash-sale 合规；
- 投资顾问服务、投资建议或收益保证；
- 盘中高频监控；
- 自动修改风险参数或自动切换到 live。

## 4. 角色与外部依赖

| 角色/系统 | 职责 |
|---|---|
| 操作者 | 创建 Robinhood 专用账户，配置参数，审阅 dry-run，人工启用 live，处理异常 |
| Phase A LLM session | 收盘后筛选候选、搜索新闻、形成 thesis，禁止下单 |
| Phase B LLM session | 开盘后重新验证、调用风险脚本、审核并按授权条件执行订单 |
| Robinhood MCP | 提供 watchlist、行情、历史数据、财务、持仓、P&L、订单和交易接口 |
| Git/GitHub | 保存 proposal、trade log 和运行间状态；也是 cloud session 的持久化媒介 |
| 调度器 | 每个交易日触发 Phase A 和 Phase B |

## 5. 功能需求

### FR-01 配置加载

- 每次运行必须重新读取 `risk_rules.json`；
- 必须使用配置中的账户号、watchlist、scan ID 和风险参数；
- 代理不得修改 `execution.mode` 或任意风险参数；
- 初始状态必须是 `dry_run`；
- 配置中的占位符在 go-live 前必须全部替换。

### FR-02 Phase A 候选构建

- 读取指定 watchlist；
- 读取 supplementary market scan，并去重；
- 对非持仓候选应用市场规模、成交量、价格、ETF 类型和 200 日趋势过滤；
- 当前持仓必须始终进入候选列表，即使不满足普通过滤条件；
- 候选数量必须受 watchlist 和 supplementary scan 的独立上限约束。

### FR-03 Phase A 信号和 thesis

候选满足以下任意条件时才进行新闻研究：

- 60 个自然日价格变动达到阈值；
- 最新成交量达到 30 日均量指定倍数；
- 当前价格接近 52 周高点或低点。

每个被研究候选必须生成结构化 thesis，至少包含：

- 日期和时间；
- 股票代码；
- thesis；
- conviction；
- invalidation；
- direction；
- risk flags；
- 距 52 周高点的百分比；
- 来源列表。

`high` conviction 必须满足 Phase A 规定的公司披露事件、新近性、反方证据、可信来源和无风险标志等全部条件。

### FR-04 Phase B 状态加载和幂等

- 读取最新 proposal、历史 trade log 和风险配置；
- 使用 proposal 自身的 `date` 作为幂等键，而不是当前执行日期；
- 已存在相同 symbol + proposal date 的风险检查或订单记录时，不得重复处理买入候选；
- stop-loss 和 take-profit 检查必须每次运行执行；
- 必须按不同日期统计 dry-run cycle 数量。

### FR-05 卖出风险控制

每个开放持仓必须检查：

- 固定或波动率缩放止损；
- 15%、30%、50% 分级止盈；
- 持仓 conviction 持续降低且超配时的 conviction trim；
- Phase A 产生的 `exit_existing`；
- 早期退出保护规则。

止损、止盈和 trim 计算必须通过对应 Python 脚本完成，不得由 LLM 在自然语言中手算。

### FR-06 买入风险控制

新建仓位和已有持仓 top-up 必须通过：

- thesis 价格与新鲜 ask 价格差检查；
- 价格相对 20 日均线的 extension 检查；
- wash-sale guard；
- sell re-entry lock；
- earnings blackout；
- 每日/每周损失限制；
- 并发持仓数和现金缓冲检查；
- conviction 仓位上限检查。

### FR-07 排序和仓位

候选排序顺序为：

1. `high`、`medium`、`low` conviction；
2. risk flags 数量少者优先；
3. 距 52 周高点更远者优先；
4. 其他情况保持输入顺序。

新仓按 conviction tier 的目标比例配置，已有仓只计算到目标仓位所需的 top-up。

### FR-08 订单执行

- 所有订单必须先调用 `review_equity_order`；
- review 出现 blocking alert 时不得下单；
- 只有 `execution.mode == live`、dry-run 周期达标且 review 无阻塞时，才允许调用 `place_equity_order`；
- 成交后必须通过 `get_equity_orders` 确认状态；
- 订单失败或数据不完整时必须跳过该候选并记录原因；
- 卖出不得因 wash-sale 阻塞。

### FR-09 审计和持久化

- Phase A 覆盖写入 `pending_proposals.jsonl`；
- Phase B 追加写入 `trade_log.jsonl`；
- 每次 Phase B 必须写入一个 `cycle_summary`；
- 必须生成 `trade_log_recent.md`；
- 运行结果必须 commit 并 push 到 `main`，push 失败不得强制覆盖远程状态。

## 6. 非功能需求

### NFR-01 可审计性

所有关键决策必须保存原始输入、脚本输出、规则结果、proposal date 和订单状态。

### NFR-02 故障安全

账户、持仓、P&L、行情或风险脚本失败时，必须停止新买入；不能猜测缺失数据。

### NFR-03 可重复性

相同脚本输入必须产生相同计算结果。核心计算脚本应保持无网络、无第三方依赖。

### NFR-04 人工授权

系统不得自行从 dry-run 切换 live。live 是操作者明确审阅后手工完成的配置变更。

### NFR-05 运行时效

Phase A 应在收盘后运行，Phase B 应在开盘后运行，以使用新鲜报价而不是前一日价格。

## 7. 当前已知限制

- 仓库没有独立调度器和 MCP 实现；
- 没有正式测试套件、回测或仿真撮合；
- dry-run 的日志状态必须与真实成交状态严格区分，否则可能出现虚拟状态和真实持仓不一致；
- Git push 不是分布式锁，不能单独防止两个运行实例并发；
- wash-sale guard 不能控制未接入流水线的账户；
- 亏损限制主要基于 realized P&L，不能替代盘中风险监控。

