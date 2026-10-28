# FriesTrader 设计文档

## 1. 设计概览

系统采用“两阶段 LLM 流程 + 确定性风险脚本 + 外部交易 MCP”的架构。

```text
Robinhood MCP
    ├── Phase A：收盘后筛选和新闻 thesis
    │       └── pending_proposals.jsonl
    └── Phase B：开盘后重新验证、风控和订单执行
            ├── scripts/*.py
            ├── trade_log.jsonl
            └── trade_log_recent.md
```

Git 仓库负责在独立的 cloud/local session 之间传递状态。Phase A 只负责建议，不得访问下单工具；Phase B 负责风险执行和审计。

## 2. 组件职责

### 2.1 `risk_rules.json`

集中保存硬性风险参数，包括：

- 账户与执行模式；
- 候选池和信号阈值；
- 仓位与现金缓冲；
- 止损和止盈；
- conviction trim；
- wash-sale、重新进入锁定和财报 blackout；
- daily/weekly loss limits。

该文件是配置源，不应被 agent 自动修改。

### 2.2 Phase A

Phase A 读取 watchlist、scan、基本面、行情和历史价格，完成：

1. 候选合并、去重和过滤；
2. 60 日价格、成交量、52 周极值信号判断；
3. 新闻和结构性风险研究；
4. thesis 与 conviction 生成；
5. 覆盖写入 `pending_proposals.jsonl`；
6. commit/push proposal。

Phase A 不执行订单，也不写 `trade_log.jsonl`。

### 2.3 Phase B

Phase B 读取最新 proposal 和历史日志，完成：

1. 加载状态并完成幂等判断；
2. 固定当前持仓分类；
3. 先执行卖出侧风险检查；
4. 先处理卖出，再重新读取账户现金和持仓；
5. 对买入候选做 weekend-gap、entry gate、loss-limit 检查；
6. 通过排序和仓位脚本分配买入；
7. review、live gate、place、fill confirm；
8. 追加日志、生成摘要、commit/push。

## 3. 数据流与状态

### 3.1 Proposal 状态

`pending_proposals.jsonl` 每次 Phase A 覆盖写入，只保存最新一轮筛选结果。主要 stage：

- `screened`：候选是否通过基础过滤；
- `signal_check`：信号原始输入与比例；
- `thesis`：用于 Phase B 的结构化建议；
- `summary`：Phase A 汇总。

### 3.2 Trade log 状态

`trade_log.jsonl` 是 append-only 审计日志。主要 stage：

- `stop_loss`；
- `take_profit`；
- `conviction_trim`；
- `loss_limit_check`；
- `risk_check`；
- `order`；
- `cycle_summary`。

每条记录应同时包含执行日期 `date`、时间 `timestamp`，涉及 proposal 的记录还应包含 `proposal_date`。

### 3.3 幂等模型

买入候选按 `symbol + proposal_date` 判断是否已经处理。当前日期不能替代 proposal date，因为 proposal 可能因 Phase A 失败而跨日重复出现。

止损和止盈是持仓状态检查，不适用普通买入幂等规则。

## 4. 风控计算组件

### 4.1 `entry_gate.py`

统一计算以下买入阻断条件：

- `entry_price_gap`：fresh ask 相对 thesis price 的涨幅；
- `entry_extension`：fresh ask 相对历史收盘均值的涨幅；
- `wash_sale_avoidance`；
- `sell_reentry_lock`；
- `earnings_blackout`。

输出 `passed`、`blocking_conditions` 和每个条件的诊断信息。

### 4.2 `pnl_pct.py`

用 net deposits 作为分母计算 daily/weekly P&L 百分比。任何达到损失阈值的情况都会设置 `entries_halted`，阻止新仓和 top-up，但不阻止风险卖出。

### 4.3 `stop_loss.py`

支持：

- fixed stop；
- volatility-scaled stop；
- 最小/最大 stop clamp；
- 历史数据不足时 fallback；
- 止盈发生后使用历史最高价作为 trailing reference。

### 4.4 `take_profit.py`

按升序检查多个收益层。单周期跨越多个 tier 时，后续 tier 使用前一 tier 扣除后的数量，避免重复按原始数量卖出。

### 4.5 `conviction_trim.py`

当连续多个周期 conviction 不是 high 且持仓显著超过目标仓位时，计算 trim 金额。

### 4.6 `rank_candidates.py`

按 conviction、risk flag 数量和距 52 周高点百分比做稳定排序。缺失字段按低优先级处理。

### 4.7 `position_sizing.py`

按排序后的候选顺序累积计算：

- 新仓金额；
- 已有仓位 top-up 金额；
- 剩余现金；
- 现金缓冲；
- 并发持仓数量。

它不负责 entry gate，也不负责订单执行。

## 5. 订单状态机

```text
candidate
   ↓
mechanical risk check
   ├── rejected → risk_check log
   └── approved
          ↓
review_equity_order
   ├── blocking alert → rejected log
   └── no blocking alert
          ↓
execution.mode + dry-run count gate
   ├── dry_run → record would_execute
   └── live → place_equity_order
                    ↓
              get_equity_orders
                    ↓
              order log with fill state
```

卖出和买入共用 review、live gate 和 fill confirmation，但卖出不能被 wash-sale 阻止。

## 6. 调度设计

推荐配置两个独立 schedule：

- Phase A：工作日约 14:30 America/Los_Angeles；
- Phase B：工作日约 06:35 America/Los_Angeles。

必须保证：

- 两个 phase 不被同一个错误 schedule 重复触发；
- 同一 phase 同一时间只有一个运行实例；
- Phase A 成功 push 后 Phase B 才能消费对应 proposal；
- 失败运行必须产生可见告警。

## 7. 安全边界

- Phase A connector 应禁用 `review_equity_order`、`place_equity_order` 和 cancel 工具；
- Phase B 不得自动修改 `risk_rules.json`；
- `place_equity_order` 必须位于 live gate 之后；
- 缺失数据时默认拒绝新买入；
- 订单 API 返回的 `unconfirmed` 不能直接作为成交结论；
- 日志写入失败或 push 失败必须报警并保留本地提交。

## 8. 设计缺口与建议

### 8.1 Dry-run 应有独立模拟状态

当前日志结构容易把“触发了止盈判断”和“真实卖出已成交”混为一谈。建议增加：

- `execution_status`: `would_execute`、`placed`、`filled`、`partially_filled`、`rejected`；
- `state_effect`: `none`、`simulated`、`actual`；
- 独立的 `paper_positions.json` 或模拟账本；
- 只有 actual fill 才能改变真实持仓历史。

### 8.2 增加配置 schema 和输入校验

所有脚本应拒绝零值、负值、非法百分比、空价格和不合法 tier，而不是依赖调用方保证输入正确。

### 8.3 增加锁和运行协调

Git push 冲突只能发现问题，不能防止并发。建议使用调度器 single-flight、分布式锁或带 lease 的运行记录。

### 8.4 增加仿真和测试

应覆盖正常路径、API 失败、部分成交、重复执行、跨日 proposal、跳空、缺历史数据和 dry-run/live 切换。

