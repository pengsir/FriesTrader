# FriesTrader Go-Live 操作手册

## 1. Go-live 原则

本手册只允许“逐步、小金额、可回滚”的上线方式。任何阶段发现日志状态、账户状态或订单状态不一致，都必须停止推进并保持 `dry_run`。

不要把通过十个 dry-run 周期理解为策略已经盈利。它只能说明流程运行过若干次，不能证明投资策略有效。

## 2. 上线前置条件

### 2.1 软件和账户

- 已拥有可控的 GitHub 仓库副本，建议设为 private；
- 已开通并验证 Robinhood Agentic Trading 专用账户；
- Robinhood MCP 已连接并能读取账户数据；
- Claude Code 或等价调度环境可运行本仓库；
- 系统时区和调度时区均明确为 `America/Los_Angeles`；
- 只有一个 Phase A schedule 和一个 Phase B schedule；
- 已建立异常通知渠道。

### 2.2 配置完成

编辑 [risk_rules.json](/Users/huipeng/projects/FriesTrader/risk_rules.json)，替换：

- `account_number`；
- `universe.watchlist_name`；
- `universe.supplementary_scan_id`；
- `wash_sale_avoidance.linked_accounts` 中的所有相关账户。

逐项复核仓位、现金、止损、止盈、亏损限制、earnings blackout 和 re-entry lock。默认值是示例，不是投资建议。

### 2.3 初始状态检查

确认：

- `execution.mode` 仍为 `dry_run`；
- `trade_log.jsonl` 和 `pending_proposals.jsonl` 中没有不明历史数据；
- 工作区无未预期修改；
- 不存在另一个 scheduler 或旧版 agent 在执行本账户；
- Git remote 指向自己控制的仓库；
- 分支策略允许运行会话 push 到 `main`。

## 3. Step 0：静态验证

在仓库根目录执行：

```bash
cd /Users/huipeng/projects/FriesTrader
python3 - <<'PY'
import ast, json, pathlib

json.load(open("risk_rules.json"))
for path in pathlib.Path("scripts").glob("*.py"):
    ast.parse(path.read_text())
    print("OK", path)
print("risk_rules.json: OK")
PY
git diff --check
git status --short
```

必须确认：

- JSON 可解析；
- Python 脚本可解析；
- 没有语法错误；
- 没有未预期的工作区修改。

## 4. Step 1：离线测试风险脚本

至少执行以下测试，并保存输出：

```bash
python3 scripts/stop_loss.py \
  --average-cost 100 \
  --current-price 92 \
  --mode volatility_scaled \
  --volatility-multiplier 2.5 \
  --min-stop-pct 0.05 \
  --max-stop-pct 0.15 \
  --fallback-stop-pct 0.07 \
  --min-bars 10 \
  --daily-closes 100,101,99,100,98,99,100,97,99,98,97

python3 scripts/take_profit.py \
  --average-cost 100 \
  --current-price 160 \
  --quantity 100 \
  --tiers '0.15:0.25,0.30:0.25,0.50:0.25'

python3 scripts/pnl_pct.py \
  --daily-realized-usd -25 \
  --weekly-realized-usd -50 \
  --net-deposits-usd 500 \
  --daily-limit-pct 0.05 \
  --weekly-limit-pct 0.10

python3 scripts/entry_gate.py \
  --fresh-ask 10 \
  --thesis-price 10 \
  --entry-price-gap-max-pct 0.03 \
  --daily-closes 9,10 \
  --max-extension-pct 0.10 \
  --today 2026-10-24
```

检查输出是否：

- 是单个合法 JSON 对象；
- 触发止损时 `triggered: true`；
- 跨越多个止盈层时数量逐层递减；
- 达到损失限制时 `entries_halted: true`；
- entry gate 正确列出 blocking conditions。

## 5. Step 2：连接器只读验收

在不允许下单的会话中逐项验证：

- 能读取 watchlist；
- 能读取 scan；
- 能读取基本面和历史行情；
- 能读取 portfolio、cash、equity value；
- 能读取 positions 和 average cost；
- 能读取 realized P&L；
- 能读取 earnings 数据；
- 能读取订单历史。

Phase A 必须在 connector 层禁用订单 review/place/cancel 工具。执行一次 Phase A，确认只产生 proposal，不产生订单调用。

## 6. Step 3：首次 Phase A dry-run

设置 Phase A schedule 后，第一次运行必须检查：

1. 日期、时间和星期来自真实系统时钟；
2. `pending_proposals.jsonl` 是本次覆盖写入，而不是旧内容追加；
3. 所有持仓都被纳入；
4. `screened`、`signal_check` 和 `thesis` 字段完整；
5. 新闻来源可访问且来源与 thesis 内容对应；
6. 没有任何订单工具被调用；
7. commit/push 成功；
8. 远程仓库的 proposal 与本地一致。

如果 Phase A push 失败，先停止 Phase B，不要手工伪造 proposal。

## 7. Step 4：首次 Phase B dry-run

首次 Phase B 必须确认：

- 读取的是最新 proposal；
- 使用 proposal date 做幂等判断；
- 先检查现有持仓的止损和止盈；
- 所有买入都经过 entry gate、loss limit、rank 和 position sizing；
- 所有订单都只执行 review，不调用 place；
- `trade_log.jsonl` 追加成功；
- 恰好有一个 `cycle_summary`；
- `trade_log_recent.md` 与 JSONL 的本次结果一致；
- commit/push 成功。

## 8. Step 5：连续观察期

至少完成 `execution.dry_run_min_cycles_before_live` 个不同日期的 dry-run 周期，默认是 10 个交易日。观察期间每天人工检查：

- proposal 是否及时更新；
- 是否出现重复 proposal 或重复 risk check；
- 是否有错误或缺失行情；
- 是否有不合理的 high conviction；
- 止损和止盈是否按预期触发；
- 现金缓冲和并发仓位是否正确；
- 周一 weekend-gap 逻辑是否正确；
- earnings blackout 是否正确阻挡买入；
- wash-sale 和 re-entry lock 是否符合账户历史；
- Git 是否持续 push 成功；
- dry-run 日志是否与真实账户状态清晰区分。

如果 dry-run 日志把未实际成交的止损/止盈当作已经成交，必须先修复状态模型，不得进入 live。

## 9. Step 6：上线前人工审批

只有操作者本人可以执行以下步骤：

1. 检查最近所有 dry-run cycle；
2. 检查所有 rejected candidate 和 stop-loss 记录；
3. 确认账户资金是可以承受全部损失的专用资金；
4. 确认仓位、现金、亏损阈值符合个人风险承受能力；
5. 确认没有重复 scheduler；
6. 确认已经准备好人工停机方式；
7. 确认券商账户和税务处理由人工负责；
8. 记录上线审批时间、审批人和当前配置版本。

## 10. Step 7：切换 live

在停止并确认没有正在运行的 Phase B 后，由人工编辑：

```json
"execution": {
  "mode": "live",
  "dry_run_min_cycles_before_live": 10
}
```

然后：

1. 查看 Git diff，确保只改变了预期配置；
2. commit 配置变更；
3. push 到受控仓库；
4. 不要让 agent 修改该字段；
5. 下一次 Phase B 仍先检查 review 和 live gate；
6. 第一笔 live 订单必须人工实时观察；
7. 确认订单状态和实际账户持仓一致。

## 11. Step 8：小金额渐进放量

首次 live 不应立即使用完整账户。建议：

- 使用专用小额账户或最低可接受金额；
- 先观察新仓、top-up、止盈和止损各类路径；
- 每天检查实际 fill price、fill quantity、现金和持仓；
- 发现任何状态不一致，立即人工把 `execution.mode` 改回 `dry_run`，并暂停 scheduler；
- 在确认日志、订单和账户状态一致前，不增加资金规模。

## 12. 紧急停机

发生以下任一情况时，立即停止新订单：

- MCP 返回异常或账户数据不完整；
- 订单状态无法确认；
- 出现重复订单；
- trade log push 失败且无法确认本地状态；
- 发现两个运行实例并发；
- 现金、仓位或账户总值与券商不一致；
- LLM 产生明显错误或无来源的 thesis；
- 发现 dry-run 状态污染真实成交历史。

操作顺序：

1. 人工将 `execution.mode` 改回 `dry_run`；
2. 停止 Phase A 和 Phase B schedule；
3. 不要删除或重写 append-only trade log；
4. 保存本地提交、错误输出和订单 ID；
5. 通过 Robinhood 账户确认实际持仓和现金；
6. 人工决定是否需要平仓；
7. 修复并重新完成观察期后，才能恢复 live。

## 13. 上线后日常检查清单

每个交易日检查：

- Phase A 是否成功；
- Phase B 是否成功；
- `cycle_summary` 是否存在且只有一条；
- 是否有未确认订单；
- 实际持仓是否与日志一致；
- 当日/当周 P&L 是否接近限制；
- 是否有异常高 conviction；
- 是否有止损、止盈和 wash-sale 事件；
- Git push 是否成功；
- 是否有运行时间漂移或调度重复。

## 14. 回滚策略

代码回滚和交易回滚必须分开处理：

- 代码问题：停止调度，保留日志，恢复经过验证的代码版本；
- 配置问题：人工恢复风险参数，禁止由 agent 自动修改；
- 订单问题：以券商真实订单和持仓为准，不以 Git 日志推测；
- 日志冲突：保留所有提交和错误信息，不使用 force push；
- 真实仓位问题：由操作者根据实际账户状态决定人工交易处理。

