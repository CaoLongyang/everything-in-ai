# Spec: scheduler（定时运行）

Status: 已批准 2026-11-21（第 7 节全部按推荐：每天 8:00、错过补跑、先只做 Windows、GitHub Actions 挪到 Backlog、只记录不通知）　版本：V1
相关：docs/ROADMAP.md V1「调度」、`docs/specs/headline-radar.md`（第一个要定时跑的任务）。

## 1. Objective

让需要定期做的事自己跑，不用手动敲命令。V1 只有一个这样的任务：**新闻雷达**（`eia headlines run`）。
对话导入需要你先去平台手动导出，不在定时范围内。

成功的样子：运行一次 `eia schedule install headlines`，之后每天早上 `inbox/headlines/` 自己出现新的大事；
电脑那时关着，开机后补跑一次；出了问题，`eia schedule status` 和试用页面能看到最近几次的结果和原因。

## 2. 两层

| 层 | 命令 | 做什么 |
|---|---|---|
| 任务 | `eia run <job>` | 跑一次指定任务，写运行记录。可以手动跑，系统定时器也是调它 |
| 定时 | `eia schedule install / list / remove / status` | 在系统的定时工具里登记、查看、删除 |

`eia run` 和定时器分开：定时器坏了、换了，任务本身不受影响；手动跑和定时跑的记录在同一个地方。

## 3. `eia run <job>`

```
eia run headlines [--vault <路径>]
```

- 先拿锁（`.eia/locks/<job>.lock`）：上一次还没跑完就直接退出并记一笔「上次还在运行，跳过」，避免两次同时写同一个文件。锁超过 2 小时视为残留，自动清掉。
- 运行记录追加到 `.eia/runs/<job>.jsonl`：开始时间、耗时、结果（成功 / 失败）、摘要（例如「新建 2，更新 3，1 个源失败」）、失败原因。**不记 key、不记新闻全文**。只保留最近 200 条。
- 退出码：成功 0，失败 1。配置错误、Ollama 没开、所有源都失败都算失败。

## 4. `eia schedule`

```
eia schedule install headlines [--vault <路径>] [--at 08:00]   # 登记（已登记的会被更新）
eia schedule list                                                # 列出 eia 登记的所有定时任务
eia schedule status [--vault <路径>]                             # 每个任务：下次什么时候跑、最近 5 次结果
eia schedule remove headlines [--vault <路径>]                   # 删除
```

**Windows（任务计划程序）**：
- 任务放在「任务计划程序库\eia\」文件夹下，名字带 vault 路径的短哈希，同一台电脑可以给多个 vault 各登记一个。
- 用 XML 登记（`schtasks /Create /XML`），因为这几项只有 XML 能设：
  - 错过的时间开机后补跑（StartWhenAvailable）；
  - 需要联网才跑；
  - 用电池时也跑，不因为拔了电源被停掉；
  - 单次最长运行 30 分钟，超时强制结束。
- 用项目 `.venv` 里的 `pythonw.exe` 跑，**不会弹出黑色命令行窗口**。
- 只以当前用户身份运行，不需要管理员权限，不保存 Windows 密码（「只在用户登录时运行」）。

**删除 vault 或换电脑**：`eia schedule list` 会标出 vault 已经不存在的任务，提示用 `remove` 清掉。

## 5. 试用页面

「新闻雷达」标签页加一块：是否已登记、下次运行时间、最近几次结果；可以一键登记 / 删除。

## 6. 测试编号（批准后登记到 docs/TEST-PLAN.md，F12）

| 编号 | 验收标准 |
|---|---|
| F12-01 | `eia run headlines` 成功和失败都写运行记录，退出码对应；记录里没有 key 和新闻全文 |
| F12-02 | 锁被占用时跳过并记录；超过 2 小时的残留锁会被清掉 |
| F12-03 | 运行记录只保留最近 200 条 |
| F12-04 | `install` 生成的任务 XML 含补跑、联网、电池、超时、`pythonw.exe`、正确的 vault 路径；重复 install 是更新而不是新增 |
| F12-05 | `list` / `status` / `remove` 正确；vault 不存在的任务被标出 |
| F12-06 | 非 Windows 系统上 `eia schedule` 说明暂不支持并给出手动做法，退出码 1；`eia run` 照常可用 |

`schtasks` 在测试里全部替换成假的，不真的登记任务；手动验收时在作者电脑上真装一次。

## 7. 作者的决定（2026-11-21，全部按推荐）

1. 默认每天 8:00 跑一次；`--at` 可以写多次，给多个时间。
2. 电脑关着或睡着错过了，开机 / 唤醒后补跑一次。
3. V1 先只做 Windows；macOS / Linux 的 `eia schedule` 给出手动配置说明（`eia run` 在所有系统上都能用）。
4. GitHub Actions 定时模板挪到 Backlog。
5. 失败只记录，在 `eia schedule status` 和试用页面里看；系统通知进 Backlog。