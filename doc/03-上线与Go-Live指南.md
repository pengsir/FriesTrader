# FriesTrader 上线与 Go-Live 指南

## 1. 上线前提

在真正切到 live trading 前，确保满足以下前提：

- 你拥有 Robinhood 账户，并已启用 Agentic Trading
- 你拥有与当前系统配套的独立交易账户
- 你已建立自己的 FriesTrader fork / template copy
- 你已在自己的 GitHub 仓库中维护该项目
- 你已经准备好 Claude Code 执行环境
- 你理解：这是一个 trading system，不是金融建议

注意：

- 不要直接用 upstream repo 运行真实资金
- 不要在未验证前直接切 live
- 不要在假数据或 placeholder 配置下上线

## 2. 先复制并配置自己的仓库

### 2.1 准备工作

1. Fork 或 Use this template 创建自己的 repo
2. Clone 到本地或直接使用 cloud routine
3. 确认该 repo 中存在：
   - risk_rules.json
   - PHASE_A_TASK.md
   - PHASE_B_TASK.md
   - scripts/
   - trade_log_template.jsonl

### 2.2 配置 risk_rules.json

至少先更新这些字段：

- account_number
- execution.mode
- dry_run_min_cycles_before_live
- universe.watchlist_name
- universe.supplementary_scan_id
- wash_sale_avoidance.linked_accounts

建议先不要大幅开阔上限，先按默认值稳步验证。

如果发现如下占位符，必须先替换：

- YOUR_ACCOUNT_NUMBER_HERE
- YOUR_WATCHLIST_NAME_HERE
- YOUR_SCAN_ID_HERE
- YOUR_OTHER_ACCOUNT_NUMBER_HERE

## 3. 准备 Robinhood watchlist 和 scan

在 Robinhood 中：

1. 创建 watchlist
2. 设置你要跟踪的标的
3. 创建一个额外的 scanner
4. 记录 scanner ID
5. 将 scanner ID 填入 universe.supplementary_scan_id

这样 Phase A 才能正常筛出候选，而不是因为无扫描器直接失败。

## 4. 先从 dry_run 开始

必须先保持：

```json
"execution": {
  "mode": "dry_run"
}
```

### 4.1 运行节奏

推荐做法：

- Phase A：工作日 2:30pm Pacific
- Phase B：工作日 6:35am Pacific

注意：云端 routine 的 cron 使用固定 UTC，不随夏令时调整。当前配置为 Phase A `30 22 * * 1-5`、
Phase B `35 14 * * 1-5`，上述时间是冬令时（标准时间）下的结果；夏令时期间（3 月至 11 月）
实际运行时间会晚 1 小时（Phase A 约 3:30pm Pacific，Phase B 约 7:35am Pacific）。
Phase B 仍在开盘之后运行，不影响正确性。如需全年固定在同一太平洋时间，需要在夏令时切换时手动调整 cron。

### 4.2 运行时要重点检查

- pending_proposals.jsonl 是否正确写出
- thesis 是否符合规则
- risk_check 是否出现异常
- order 记录是否出现在 dry_run 模式中
- 是否有 candidate 被错误买入或卖出
- 是否出现 loss_limit halt
- 是否出现未能解释的 stop_loss / take_profit / conviction_trim

此阶段的目的不是“证明系统能赚钱”，而是：

- 证明系统按规则运行
- 证明 AI 不是在随意发单
- 证明日志和状态是正确的

## 5. 评审 dry-run 结果

在真实切到 live 之前，至少检查以下内容：

- trade_log.jsonl 是否 append 正常
- cycle_summary 是否存在
- risk_check 是否有明显误判
- entry_gate 是否误堵或误放
- stop_loss 是否过于敏感或过于迟钝
- take_profit 是否在错误时机触发
- top-up 是否在错误条件下审批
- wash-sale 逻辑是否被误用

建议：

- 不只看 summary
- 一定要查看原始 trade_log.jsonl
- 有问题先修规则，不要直接切 live

## 6. 上线前的最低要求

只有在以下条件都满足时，才考虑 live：

- dry_run 已至少运行规定的 cycle 数量
- risk_log / trade_log 看起来稳定
- AI 的 thesis 逻辑没明显偏离规则
- 没有重复 scheduler
- 你已确认 risk_rules.json 中没有占位符
- 你已经审查过关键规则配置

如果系统还未稳定，首先收紧风控，而不是直接打开 live。

## 7. 手工切到 live

一旦你确认 dry_run 稳定：

- 手工修改 risk_rules.json
- 将 execution.mode 改为 "live"

非常关键：

- 这个动作必须由人类执行
- AI 不允许自动修改这项设置
- live mode 不能靠“自我授权”打开

## 8. 运行 live 工作流

### 8.1 Phase A

- 每个工作日 2:30pm Pacific
- 负责筛股和 thesis 生成
- 不允许下订单

### 8.2 Phase B

- 每个工作日 6:35am Pacific
- 负责 risk verification, ranking, execution gate, logging
- 只有在满足条件时才执行真实订单

## 9. live 运行后的检查

每次 live run 后，确认：

- 是否有 order 记录
- 是否有实际下单执行
- 是否有 review_equity_order 的拒绝或通过
- 是否出现 loss limit halt
- 是否出现无效的 stop-loss / take-profit / conviction-trim
- 是否有 wash sale flag
- order_state 是否已被确认

如果发现异常：

- 先停住
- 审查日志
- 修正配置或逻辑
- 不要在错误状态持续放大

## 10. 调度注意事项

不要让两个 scheduler 同时触发同一 Phase：

- 会造成重复 proposal
- 会重复写日志
- 会重复发单
- 会带来 race condition

建议：

- 一个 phase 一套 scheduler
- 启动和关闭都要清晰
- 如果有自动化任务，最好有单一入口

## 11. Git 规范与风险控制

执行过程中，不要做以下操作：

- git reset --hard
- git checkout 试图修复状态
- git switch 试图改分支
- git stash
- merge 作为修复方案
- force push

因为在 live trading 环境中，这些操作可能导致：

- 丢失当前执行状态
- 造成真实订单与提交状态脱节
- 让自动运行停在半中断状态

系统要求是：

- 维持现有状态
- 记录错误
- 基于日志恢复而不是强制修正状态

## 12. 上线前最终检查清单

在正式上线前，请逐项确认：

- [ ] account_number 已填写
- [ ] watchlist 已准备
- [ ] supplementary scan 已创建并填入
- [ ] risk_rules.json 中没有占位符
- [ ] execution.mode 仍为 dry_run
- [ ] 已完成足够次数的 dry_run
- [ ] 已级别审查 trade_log.jsonl
- [ ] 未出现明显的误判/误放
- [ ] 只用一套 scheduler 运行每个 Phase
- [ ] 人类确认准备切到 live

## 13. 生产上线原则

FriesTrader 的线上原则可以概括为一句话：

AI 负责筛选与推理，
脚本负责验证与门槛，
人类负责最终启用 live trading。

如果无法满足这一原则，则不要上线。

## 14. 完成上线后的持续维护

上线后，你需要：

- 按日检查 trade_log.jsonl
- 检查 stop loss / take profit 是否符合预期
- 检查 candidate 排序是否合理
- 检查 loss limit 是否阻止了无效新仓位
- 检查是否存在过多无解释的拒绝或批准
- 视需要收紧 risk_rules.json

## 15. 结论

FriesTrader 的 go-live 并不是“把 AI 直接放进真实资金”的动作，而是：

- 先验证 dry-run 几轮
- 再人工确认规则稳定
- 再用人工权限切换到 live
- 最后持续审计 trade_log.jsonl

这确保系统在真实交易开始前，已经具备已知规则和对数值过程的控制能力。
