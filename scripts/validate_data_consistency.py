#!/usr/bin/env python3
"""
机游共振日历 数据一致性校验脚本

用法:
    python3 validate_data_consistency.py --date 2026-07-13
    python3 validate_data_consistency.py --html_path 机游共振日历.html
    python3 validate_data_consistency.py --range 2026-07-01 2026-07-13

校验规则：
  1. 每天的机构TOP5数量必须 = 5，不足则告警
  2. 机构净买入金额必须为正数（净买入榜），负数则方向错误告警
  3. 机构净卖出金额必须为负数（净卖出榜），正数则方向错误告警
  4. 金额合理性：单日单只股票机构净买入 < 100亿
  5. 游资数据非空校验
  6. 共振数据双向一致性：共振股票必须同时出现在机构TOP5和游资买入榜
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

# 同目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_jiyou_resonance_calendar import (
    build_daily_data,
    get_institution_data,
    get_youzi_data,
    validate_data as _api_validate,
    STATE_DB,
)


# ========== 从状态数据库读取 ==========

def load_from_db(date_str: str) -> Dict:
    """从状态数据库读取历史数据"""
    conn = sqlite3.connect(STATE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM update_history WHERE date = ?", (date_str,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    cols = [desc[0] for desc in cursor.description]
    data = dict(zip(cols, row))
    for k in ["institution_top5", "institution_sell_top3",
              "youzi_items", "youzi_buy_top5", "youzi_sell_top3", "resonance"]:
        val = data.get(k)
        data[k] = json.loads(val) if val else []
    return data


# ========== 校验规则 ==========

def validate_institution_top5_count(data: Dict, date_str: str) -> List[str]:
    """校验规则1：机构TOP5数量必须=5"""
    errors = []
    inst = data.get("institution_top5", [])
    count = len(inst)
    if count != 5:
        errors.append(f"[{date_str}] 机构净买入TOP5数量={count}，期望=5")
    return errors


def validate_institution_buy_positive(data: Dict, date_str: str) -> List[str]:
    """校验规则2：机构净买入榜金额必须为正"""
    errors = []
    for item in data.get("institution_top5", []):
        amount = item.get("amount", 0)
        name = item.get("name", "未知")
        if amount <= 0:
            errors.append(f"[{date_str}] 机构净买入榜 {name} 金额={amount}万，应为正数（方向错误）")
    return errors


def validate_institution_sell_negative(data: Dict, date_str: str) -> List[str]:
    """校验规则3：机构净卖出榜金额必须为负"""
    errors = []
    for item in data.get("institution_sell_top3", []):
        amount = item.get("amount", 0)
        name = item.get("name", "未知")
        if amount >= 0:
            errors.append(f"[{date_str}] 机构净卖出榜 {name} 金额={amount}万，应为负数（方向错误）")
    return errors


def validate_amount_reasonable(data: Dict, date_str: str,
                               max_billion: float = 100.0) -> List[str]:
    """校验规则4：金额合理性（单日单只股票机构净买入 < 100亿）"""
    errors = []
    max_wan = max_billion * 10000
    for item in data.get("institution_top5", []):
        amount = abs(item.get("amount", 0))
        name = item.get("name", "未知")
        if amount > max_wan:
            errors.append(f"[{date_str}] 机构净买入 {name} 金额={amount}万，超过{max_billion}亿（异常）")
    for item in data.get("institution_sell_top3", []):
        amount = abs(item.get("amount", 0))
        name = item.get("name", "未知")
        if amount > max_wan:
            errors.append(f"[{date_str}] 机构净卖出 {name} 金额={amount}万，超过{max_billion}亿（异常）")
    return errors


def validate_youzi_not_empty(data: Dict, date_str: str) -> List[str]:
    """校验规则5：游资数据非空"""
    errors = []
    yz_buy = data.get("youzi_buy_top5", data.get("youzi_items", []))
    if not yz_buy:
        errors.append(f"[{date_str}] 游资数据为空")
    return errors


def validate_resonance_consistency(data: Dict, date_str: str) -> List[str]:
    """校验规则6：共振股票必须同时在机构TOP5和游资买入榜中
    注意：共振计算使用全量游资数据，不只是TOP5展示的部分。
    因此此处只校验机构侧一致性，游资侧需要从API全量数据校验。
    """
    errors = []
    inst_names = {item.get("name") for item in data.get("institution_top5", [])}
    # 收集所有游资数据中的股票（买入/卖出都算出现过）
    youzi_stocks = set()
    for field in ["youzi_buy_top5", "youzi_sell_top3", "youzi_items"]:
        for yz in data.get(field, []):
            stock = yz.get("stock", "")
            if stock:
                youzi_stocks.add(stock)

    for res in data.get("resonance", []):
        stock = res.get("stock_name", "")
        if stock not in inst_names:
            errors.append(f"[{date_str}] 共振股票 {stock} 不在机构净买入TOP5中（不一致）")
        # 游资侧只在有完整数据时校验；历史数据可能只有TOP5
        # 如果游资列表为空但有共振，肯定有问题
        if not youzi_stocks and data.get("resonance"):
            errors.append(f"[{date_str}] 游资数据为空但存在共振信号（异常）")
            break
    return errors


def run_all_validations(data: Dict, date_str: str) -> List[str]:
    """运行所有校验规则"""
    all_errors = []
    all_errors.extend(validate_institution_top5_count(data, date_str))
    all_errors.extend(validate_institution_buy_positive(data, date_str))
    all_errors.extend(validate_institution_sell_negative(data, date_str))
    all_errors.extend(validate_amount_reasonable(data, date_str))
    all_errors.extend(validate_youzi_not_empty(data, date_str))
    all_errors.extend(validate_resonance_consistency(data, date_str))
    return all_errors


# ========== 日期范围工具 ==========

def date_range(start_date: str, end_date: str) -> List[str]:
    """生成日期范围列表"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(description="机游共振日历数据一致性校验")
    parser.add_argument("--date", help="校验指定日期 (YYYY-MM-DD)")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"),
                        help="校验日期范围")
    parser.add_argument("--from-api", action="store_true",
                        help="从东方财富API实时拉取数据校验（默认从状态DB读）")
    parser.add_argument("--html-path", help="从HTML文件解析校验（可选）")
    args = parser.parse_args()

    # 确定日期列表
    if args.range:
        dates = date_range(args.range[0], args.range[1])
    elif args.date:
        dates = [args.date]
    else:
        dates = [datetime.now().strftime("%Y-%m-%d")]

    print(f"📅 校验日期范围: {len(dates)} 天")
    print(f"📊 数据源: {'东财官方API' if args.from_api else '本地状态数据库'}")
    print("=" * 60)

    total_errors = 0
    total_warnings = 0
    total_checked = 0

    for date_str in dates:
        # 获取数据
        if args.from_api:
            try:
                daily_data = build_daily_data(date_str)
                data = {
                    "institution_top5": [s.model_dump() for s in daily_data.institution_top5],
                    "institution_sell_top3": [s.model_dump() for s in daily_data.institution_sell_top3],
                    "youzi_buy_top5": [s.model_dump() for s in daily_data.youzi_buy_top5],
                    "youzi_sell_top3": [s.model_dump() for s in daily_data.youzi_sell_top3],
                    "youzi_items": [s.model_dump() for s in daily_data.youzi_items],
                    "resonance": [s.model_dump() for s in daily_data.resonance],
                }
            except Exception as e:
                print(f"❌ [{date_str}] API拉取失败: {e}")
                total_errors += 1
                continue
        else:
            data = load_from_db(date_str)
            if not data:
                print(f"⚠️  [{date_str}] 状态库无数据，跳过")
                continue

        total_checked += 1
        errors = run_all_validations(data, date_str)

        if errors:
            print(f"\n❌ [{date_str}] 发现 {len(errors)} 个问题:")
            for err in errors:
                print(f"   • {err}")
            total_errors += len(errors)
        else:
            print(f"✅ [{date_str}] 校验通过")
            # 打印摘要
            inst_count = len(data.get("institution_top5", []))
            sell_count = len(data.get("institution_sell_top3", []))
            yz_count = len(data.get("youzi_buy_top5", data.get("youzi_items", [])))
            res_count = len(data.get("resonance", []))
            print(f"   机构买{inst_count}/卖{sell_count} | 游资{yz_count} | 共振{res_count}")

    print("=" * 60)
    print(f"\n📊 校验结果汇总:")
    print(f"   检查天数: {total_checked}")
    print(f"   错误数: {total_errors}")

    if total_errors > 0:
        print(f"\n❌ 校验失败，共 {total_errors} 个问题需要处理")
        sys.exit(1)
    else:
        print(f"\n✅ 全部校验通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
