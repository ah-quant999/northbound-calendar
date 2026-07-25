# 🛡️ 核心资产保护清单

> **铁律：以下文件属于核心资产，严禁随意删除、覆盖或重命名。**
> 删除前必须确认、必须备份、必须通过 `python3 pre_deploy_smoke_test.py` 验证。

---

## 📄 核心页面（5个）

- ✅ `北向资金日历.html`
- ✅ `index.html`
- ✅ `jiyou-resonance.html`
- ✅ `northbound-analysis.html`
- ✅ `jiyou-signal-analysis.html`

## 📄 其他重要页面

- ✅ `重要日历.html`
- ✅ `northbound-backtest.html`
- ✅ `northbound-industry-backtest.html`
- ✅ `resonance-backtest.html`
- ✅ `portal.html`
- ✅ `signal-guide.html`
- ✅ `daily-insight.html`

## ⚙️ 核心生成脚本（scripts/）

- ✅ `scripts/update_northbound_calendar.py`
- ✅ `scripts/update_jiyou_resonance_calendar.py`
- ✅ `scripts/update_important_calendar.py`
- ✅ `scripts/full_deploy.sh`
- ✅ `scripts/daily_update_northbound_gha.py`
- ✅ `scripts/daily_update_jiyou_gha.py`
- ✅ `scripts/update_important_gha.py`
- ✅ `scripts/t1_morning_fallback.py`
- ✅ `scripts/northbound_analysis.py`
- ✅ `scripts/jiyou_signal_analysis.py`
- ✅ `scripts/generate_northbound_backtest_page.py`
- ✅ `scripts/generate_northbound_industry_backtest_page.py`
- ✅ `scripts/daily_insight.py`
- ✅ `scripts/daily_recheck.py`

## ⚙️ 验证脚本

- ✅ `pre_deploy_smoke_test.py`
- ✅ `scripts/validate_calendar_html.py`
- ✅ `scripts/validate_data_consistency.py`
- ✅ `scripts/validate_date_alignment.py`
- ✅ `scripts/validate_format_uniformity.py`

## 🔄 GHA工作流（.github/workflows/）

- ✅ `.github/workflows/daily_update.yml`
- ✅ `.github/workflows/northbound_daily_update.yml`
- ✅ `.github/workflows/important_monthly_update.yml`
- ✅ `.github/workflows/t1_morning_fallback.yml`
- ✅ `.github/workflows/weekly_industry_refresh.yml`

## 📊 数据源配置

- ✅ `scripts/calendar_git.py`
- ✅ `scripts/stock_industry.py`
- ✅ `scripts/northbound_lhb_tracker.py`

## 🔐 凭证与配置

- ⚠️ `.github/workflows/secrets（GitHub Secrets）`
- ⚠️ `环境变量：GITHUB_TOKEN / Tushare Token 等`

---

**共 41 项核心资产**

## 🚨 删除/覆盖前检查清单

1. **确认文件不在本清单中**，或确认删除意图正确
2. **先备份**：复制一份到 `backup/` 目录，带时间戳
3. **本地验证**：`python3 pre_deploy_smoke_test.py` 全部通过
4. **Git提交**：确保当前状态已提交，可回退
5. **通知主人**：涉及核心算法/数据源变更必须先告知

## 🔒 保护脚本

运行 `bash protect_critical_files.sh` 可给所有核心文件加只读保护（chmod 444）。
需要修改时运行 `bash protect_critical_files.sh unlock` 临时解锁。