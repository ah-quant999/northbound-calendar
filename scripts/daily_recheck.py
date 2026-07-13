#!/usr/bin/env python3
"""
北向+机游日历兜底复核脚本
功能：交易日T+1的07:30自动运行，对北向和机游日历做一次兜底更新
运行逻辑：先跑北向（--force --date=T日），再跑机游（--force --date=T日）

参数：
  result_mode (必需，第一个参数): display_only / notify / auto
  --date: T日日期 (格式: YYYY-MM-DD, 默认: 当前日期-1天)
  --nb_script: 北向脚本路径 (默认: ./codeact/scripts/update_northbound_calendar.py)
  --jy_script: 机游脚本路径 (默认: ./codeact/scripts/update_jiyou_resonance_calendar.py)

result_mode: auto
"""

import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from codeact_sdk import CodeActSDK

# ========== A股法定假日集合（与北向/机游脚本保持一致） ==========
# 来源：上海证券交易所2026年休市安排
# https://www.sse.com.cn/disclosure/dealinstruc/closed
A_STOCK_HOLIDAYS_2026 = {
    # 元旦：1月1日-3日
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节：2月15日-23日
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-23",
    # 清明节：4月4日-6日
    "2026-04-06",
    # 劳动节：5月1日-5日
    "2026-05-01", "2026-05-04", "2026-05-05",
    # 端午节：6月19日-21日
    "2026-06-19",
    # 中秋节：9月25日-27日
    "2026-09-25",
    # 国庆节：10月1日-7日
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}

# 补充：港股独立休市日（北向通道关闭，但A股正常交易）
HK_HOLIDAYS_2026 = {
    "2026-07-01",  # 香港回归纪念日
}


def is_trading_day(date_str: str) -> bool:
    """判断指定日期是否为A股交易日（排除周末+法定假日）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    # 周末排除
    if dt.weekday() >= 5:
        return False
    # 法定假日排除
    if date_str in A_STOCK_HOLIDAYS_2026 or date_str in HK_HOLIDAYS_2026:
        return False
    return True


def run_script(script_path: str, date: str, label: str) -> dict:
    """
    运行单个日历更新脚本，返回执行结果。
    使用 subprocess.run 调用脚本，传入 --force --date 参数。
    """
    # 内层脚本用 no_reply 模式，避免其 submit_result 干扰外层脚本的最终结果
    cmd = [
        sys.executable,
        script_path,
        "--force",
        "--date", date,
        "--result_mode", "no_reply",
    ]
    print(f"🚀 [{label}] 执行: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        exit_code = result.returncode

        # 只输出关键行
        for line in stdout.split("\n"):
            if any(kw in line for kw in ["✅", "❌", "📅", "📄", "📤", "⚠️", "🏛️", "📊"]):
                print(f"  [{label}] {line}")

        if stderr:
            print(f"  [{label}] stderr: {stderr[:500]}")

        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "success": exit_code == 0,
        }
    except subprocess.TimeoutExpired:
        print(f"  [{label}] ⏰ 执行超时（5分钟）")
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "Timeout: 脚本执行超过5分钟",
            "success": False,
        }
    except Exception as e:
        print(f"  [{label}] ❌ 执行异常: {e}")
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "success": False,
        }


async def main():
    # ========== 参数解析 ==========
    result_mode = sys.argv[1] if len(sys.argv) > 1 else "auto"

    # 解析其他命名参数
    target_date = None
    nb_script = "./codeact/scripts/update_northbound_calendar.py"
    jy_script = "./codeact/scripts/update_jiyou_resonance_calendar.py"

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--date" and i + 1 < len(sys.argv):
            target_date = sys.argv[i + 1]
            i += 2
        elif arg == "--nb_script" and i + 1 < len(sys.argv):
            nb_script = sys.argv[i + 1]
            i += 2
        elif arg == "--jy_script" and i + 1 < len(sys.argv):
            jy_script = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    # 如果未指定 --date，默认取昨天（T日 = 当前日期-1天）
    if not target_date:
        tz = timezone(timedelta(hours=8))
        yesterday = datetime.now(tz) - timedelta(days=1)
        target_date = yesterday.strftime("%Y-%m-%d")

    # 映射 result_mode
    actual_mode = result_mode if result_mode != "auto" else "display_only"

    print(f"📅 复核日期（T日）: {target_date}")
    print(f"🔧 result_mode: {result_mode} → {actual_mode}")
    print(f"📄 北向脚本: {nb_script}")
    print(f"📄 机游脚本: {jy_script}")

    sdk = CodeActSDK()

    try:
        # ========== 步骤1：检查T日是否为交易日 ==========
        if not is_trading_day(target_date):
            print(f"⏭️ {target_date} 不是交易日，跳过复核")
            await sdk.submit_result(
                result_mode="no_reply",
                status="success",
                message=f"NO_REPLY: {target_date} 非交易日，跳过复核",
                data={"date": target_date, "is_trading_day": False, "skipped_reason": "非交易日"},
            )
            return

        print(f"✅ {target_date} 是交易日，执行复核")

        # ========== 步骤2：执行北向日历复核 ==========
        print("\n" + "=" * 50)
        print("📊 步骤1/2：北向资金日历复核")
        print("=" * 50)
        nb_result = run_script(nb_script, target_date, "北向")

        # ========== 步骤3：执行机游共振日历复核 ==========
        print("\n" + "=" * 50)
        print("📊 步骤2/2：机游共振日历复核")
        print("=" * 50)
        jy_result = run_script(jy_script, target_date, "机游")

        # ========== 步骤4：汇总结果 ==========
        nb_ok = nb_result["success"]
        jy_ok = jy_result["success"]

        # 从 stdout 中提取关键信息
        nb_summary = ""
        jy_summary = ""
        for line in nb_result["stdout"].split("\n"):
            if "✅" in line or "⚠️" in line or "🏛️" in line:
                nb_summary = line.strip()
                break
        for line in jy_result["stdout"].split("\n"):
            if "✅" in line or "⚠️" in line or "🏛️" in line:
                jy_summary = line.strip()
                break

        # 构建结果消息
        total_ok = nb_ok and jy_ok
        status_parts = []

        if nb_ok:
            status_parts.append(f"✅ 北向: 完成")
        else:
            status_parts.append(f"❌ 北向: 失败 (exit_code={nb_result['exit_code']})")

        if jy_ok:
            status_parts.append(f"✅ 机游: 完成")
        else:
            status_parts.append(f"❌ 机游: 失败 (exit_code={jy_result['exit_code']})")

        status_line = " | ".join(status_parts)

        message_parts = [
            f"📋 [{target_date}] 日历兜底复核结果\n",
            f"{status_line}\n",
        ]
        if nb_summary:
            message_parts.append(f"\n北向: {nb_summary}")
        if jy_summary:
            message_parts.append(f"\n机游: {jy_summary}")

        # 详细日志（非完整 stdout）
        if not nb_ok and nb_result["stderr"]:
            message_parts.append(f"\n北向异常: {nb_result['stderr'][:200]}")
        if not jy_ok and jy_result["stderr"]:
            message_parts.append(f"\n机游异常: {jy_result['stderr'][:200]}")

        message = "".join(message_parts)
        print(f"\n📋 复核结果: {status_line}")

        if total_ok:
            # 全部成功
            await sdk.submit_result(
                result_mode=actual_mode,
                status="success",
                message=message,
                data={
                    "date": target_date,
                    "is_trading_day": True,
                    "northbound_success": nb_ok,
                    "jiyou_success": jy_ok,
                    "northbound_exit_code": nb_result["exit_code"],
                    "jiyou_exit_code": jy_result["exit_code"],
                },
            )
        else:
            # 部分失败 → 使用 notify 让主 Agent 处理
            await sdk.submit_result(
                result_mode="notify",
                status="success",
                message=message,
                data={
                    "date": target_date,
                    "is_trading_day": True,
                    "northbound_success": nb_ok,
                    "jiyou_success": jy_ok,
                    "northbound_exit_code": nb_result["exit_code"],
                    "jiyou_exit_code": jy_result["exit_code"],
                    "northbound_stderr": nb_result["stderr"][:500] if not nb_ok else "",
                    "jiyou_stderr": jy_result["stderr"][:500] if not jy_ok else "",
                },
            )

    except Exception as e:
        print(f"❌ 复核脚本执行失败: {e}")
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"日历兜底复核执行失败: {e}",
            data={"error_type": type(e).__name__, "date": target_date},
        )


if __name__ == "__main__":
    asyncio.run(main())