#!/usr/bin/env python3
"""
股票行业映射表管理

- 本地JSON缓存：data/stock_industry.json  {code: industry_name}
- 数据源：东方财富 F10 公司概况接口（EM2016字段，东财三级行业分类）
- 增量更新：每天跑分析时遇到新股票自动补查
- 全量更新：每周日跑一次，遍历所有已知股票刷新行业

用法：
  python3 stock_industry.py --check CODES        # 检查这些代码的行业，缺失的补查
  python3 stock_industry.py --refresh-all        # 全量刷新所有已知股票行业
  python3 stock_industry.py --get 600519         # 查单只股票行业
"""

import json
import time
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
CACHE_FILE = DATA_DIR / "stock_industry.json"

F10_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def load_cache() -> Dict[str, str]:
    """加载行业缓存"""
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: Dict[str, str]) -> None:
    """保存行业缓存"""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def _code_to_market(code: str) -> str:
    """股票代码转市场前缀 SH/SZ"""
    code = code.strip()
    if code.startswith(("6", "9")):
        return f"SH{code}"
    elif code.startswith(("0", "2", "3")):
        return f"SZ{code}"
    elif code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SH{code}"


def fetch_industry(code: str) -> Optional[str]:
    """从东财F10获取股票行业（取一级分类）"""
    market_code = _code_to_market(code)
    try:
        resp = requests.get(
            F10_URL,
            params={"code": market_code},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        jbzl = data.get("jbzl", [])
        if not jbzl:
            return None
        em2016 = jbzl[0].get("EM2016", "")
        if not em2016:
            return None
        # EM2016 格式：一级-二级-三级，取一级
        return em2016.split("-")[0].strip()
    except Exception as e:
        print(f"  [WARN] 获取 {code} 行业失败: {e}", file=sys.stderr)
        return None


def get_industry(code: str, cache: Dict[str, str],
                 auto_fill: bool = True) -> Optional[str]:
    """
    获取股票行业
    - 优先从缓存读
    - 缓存没有且auto_fill=True时，调用API补查并写入缓存
    """
    code = code.strip()
    if code in cache:
        return cache[code] or None

    if not auto_fill:
        return None

    industry = fetch_industry(code)
    if industry:
        cache[code] = industry
    else:
        cache[code] = ""  # 查不到也记下来，避免重复查
    return industry


def fill_missing(codes: List[str], cache: Optional[Dict[str, str]] = None,
                 delay: float = 0.1) -> Dict[str, str]:
    """
    批量补查缺失的行业信息
    返回更新后的缓存
    """
    if cache is None:
        cache = load_cache()

    missing = [c.strip() for c in codes if c.strip() not in cache]
    missing = list(dict.fromkeys(missing))  # 去重保序

    if not missing:
        return cache

    print(f"补查 {len(missing)} 只股票行业...", file=sys.stderr)
    for i, code in enumerate(missing):
        get_industry(code, cache, auto_fill=True)
        if (i + 1) % 20 == 0:
            print(f"  进度 {i+1}/{len(missing)}", file=sys.stderr)
            save_cache(cache)  # 中间保存
        time.sleep(delay)

    save_cache(cache)
    print(f"完成，缓存共 {len(cache)} 只", file=sys.stderr)
    return cache


def refresh_all() -> Dict[str, str]:
    """全量刷新所有已知股票的行业"""
    cache = load_cache()
    codes = list(cache.keys())
    if not codes:
        print("缓存为空，无需刷新", file=sys.stderr)
        return cache

    print(f"全量刷新 {len(codes)} 只股票行业...", file=sys.stderr)
    for i, code in enumerate(codes):
        industry = fetch_industry(code)
        if industry:
            cache[code] = industry
        if (i + 1) % 50 == 0:
            print(f"  进度 {i+1}/{len(codes)}", file=sys.stderr)
            save_cache(cache)
        time.sleep(0.05)

    save_cache(cache)
    print(f"全量刷新完成", file=sys.stderr)
    return cache


def main():
    parser = argparse.ArgumentParser(description="股票行业映射表管理")
    parser.add_argument("--check", nargs="+", help="检查并补查这些股票代码")
    parser.add_argument("--refresh-all", action="store_true", help="全量刷新")
    parser.add_argument("--get", help="查单只股票行业")
    args = parser.parse_args()

    if args.get:
        cache = load_cache()
        ind = get_industry(args.get, cache)
        save_cache(cache)
        print(ind or "未知")
        return

    if args.check:
        fill_missing(args.check)
        return

    if args.refresh_all:
        refresh_all()
        return


if __name__ == "__main__":
    main()
