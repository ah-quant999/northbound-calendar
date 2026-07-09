#!/usr/bin/env python3
"""
生成19个月（2026年6月~2027年12月）重要日历HTML文件。
每个HTML文件包含18类事件标签导航栏、月历表格、底部18个色块标注区。

用法:
    python generate_calendars.py [--start-year 2026] [--start-month 6] [--end-year 2027] [--end-month 12] [--output-dir ...]
"""

import argparse
import calendar
import os
from datetime import datetime, date, timedelta

# ==================== 工具函数 ====================

def get_third_friday(year, month):
    """获取某月第三个周五的日期"""
    c = calendar.monthcalendar(year, month)
    fridays = []
    for week in c:
        if week[calendar.FRIDAY] != 0:
            fridays.append(week[calendar.FRIDAY])
    if len(fridays) >= 3:
        return fridays[2]
    return fridays[-1]  # fallback

def get_last_working_day(year, month):
    """获取某月最后一个工作日（周一至周五）"""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() >= 5:  # 周六=5, 周日=6
        d -= timedelta(days=1)
    return d.day

def get_second_last_working_day(year, month):
    """获取某月倒数第二个工作日"""
    last = get_last_working_day(year, month)
    d = date(year, month, last)
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.day

def is_weekend(y, m, d):
    """判断某天是否为周末"""
    return date(y, m, d).weekday() >= 5

def get_weekday_name(d):
    """获取星期几的中文名"""
    names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    return names[d.weekday()]

def get_week_label(week_num, days_in_month, year, month):
    """生成周标签"""
    first_day = date(year, month, 1)
    start_day = 1 + week_num * 7 - first_day.weekday()
    end_day = min(start_day + 6, days_in_month)
    if start_day < 1:
        # 跨月，显示上个月日期
        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month = 12
            prev_year -= 1
        prev_days = calendar.monthrange(prev_year, prev_month)[1]
        start_label = prev_days + start_day
    else:
        start_label = start_day
    if end_day > days_in_month:
        end_label = end_day - days_in_month
    else:
        end_label = end_day
    return f"第{week_num + 1}周 {start_label}/{month}-{end_label}/{month}"

# ==================== 事件数据定义 ====================

# ---- 2026年已知数据 ---- #

HOLIDAYS_2026 = {
    'hk': {
        # 港股休市2026
        1: [1],
        2: [16, 17, 18, 19],  # 2/16半日，17-19全日
        4: [3, 6, 7],
        5: [1, 25],
        6: [19],
        7: [1],
        9: [25],
        10: [1, 19],
        12: [24, 25, 31],  # 12/24半日, 12/31半日
    },
    'us': {
        # 美股休市2026
        1: [1],
        2: [16],
        4: [3],
        5: [25],
        6: [19],
        7: [3],
        9: [7],
        11: [26, 27],  # 11/27早收1pm
        12: [24, 25],  # 12/24早收1pm
    },
    'sg': {
        # SG公假2026
        1: [1],
        2: [17, 18],
        3: [21],
        4: [3],
        5: [1, 27],
        6: [1],
        8: [10],
        11: [9],
        12: [25],
    },
    'cn': {
        # A股休市2026
        1: [1, 2, 3],
        2: [15, 16, 17, 18, 19, 20, 21, 22, 23],
        4: [4, 5, 6],
        5: [1, 2, 3, 4, 5],
        6: [19, 20, 21],
        9: [25, 26, 27],
        10: [1, 2, 3, 4, 5, 6, 7],
    }
}

FOMC_2026 = {
    # (开始日, 决议日, 描述)
    1: [(28, 29, 'FOMC利率决议 北京时间1/29凌晨')],
    3: [(18, 19, 'FOMC利率决议+点阵图/SEP 北京时间3/19凌晨')],
    4: [(29, 30, 'FOMC利率决议 北京时间4/30凌晨')],
    6: [(17, 18, 'FOMC利率决议+点阵图/SEP 北京时间6/18凌晨')],
    7: [(29, 30, 'FOMC利率决议 北京时间7/30凌晨')],
    9: [(16, 17, 'FOMC利率决议+点阵图/SEP 北京时间9/17凌晨')],
    10: [(28, 29, 'FOMC利率决议 北京时间10/29凌晨')],
    12: [(9, 10, 'FOMC利率决议+点阵图/SEP 北京时间12/10凌晨')],
}

# ---- 2027年已知数据 ---- #

HOLIDAYS_2027 = {
    'hk': {
        # 港股休市2027
        1: [1],
        2: [6, 7, 8, 9],
        3: [26, 27, 29],
        4: [5],
        5: [1, 13],
        6: [9],
        7: [1],
        9: [16],
        10: [1, 8],
        12: [25, 27],
    },
    'us': {
        # 美股休市2027
        1: [1],
        2: [15],
        3: [26],
        5: [31],
        6: [18],
        7: [5],
        9: [6],
        11: [25, 26],  # 11/26早收
        12: [24, 25],
    },
    'sg': {
        # SG公假2027
        1: [1],
        2: [6, 7, 8],
        3: [10, 26],
        5: [1, 17, 20],
        8: [9],
        10: [28],
        12: [25],
    },
    'cn': {
        # A股休市2027（暂定，待官方公布确认）
        1: [1, 2, 3],
        2: [13, 14, 15, 16, 17, 18, 19, 20, 21],  # 春节推算
        4: [4, 5],
        5: [1, 2, 3, 4, 5],
        6: [12, 13, 14],  # 端午推算
        9: [24, 25, 26],  # 中秋推算
        10: [1, 2, 3, 4, 5, 6, 7],
    }
}

FOMC_2027 = {
    1: [(27, 28, 'FOMC利率决议 北京时间1/28凌晨')],
    3: [(17, 18, 'FOMC利率决议+点阵图/SEP 北京时间3/18凌晨')],
    4: [(28, 29, 'FOMC利率决议 北京时间4/29凌晨')],
    6: [(9, 10, 'FOMC利率决议+点阵图/SEP 北京时间6/10凌晨')],
    7: [(28, 29, 'FOMC利率决议 北京时间7/29凌晨')],
    9: [(15, 16, 'FOMC利率决议+点阵图/SEP 北京时间9/16凌晨')],
    10: [(27, 28, 'FOMC利率决议 北京时间10/28凌晨')],
    12: [(8, 9, 'FOMC利率决议+点阵图/SEP 北京时间12/9凌晨')],
}

# ==================== 事件生成函数 ====================

def get_recurring_events(year, month):
    """生成每月重复事件"""
    events = []
    days_in_month = calendar.monthrange(year, month)[1]
    third_fri = get_third_friday(year, month)
    second_last = get_second_last_working_day(year, month)

    # PMI - 每月最后一天
    events.append({
        'day': days_in_month,
        'cls': 'macro',
        'text': '🇨🇳 中国PMI制造业/非制造业数据'
    })

    # CPI/PPI - 每月9日前后
    cpi_day = 9
    if cpi_day <= days_in_month:
        events.append({
            'day': cpi_day,
            'cls': 'macro',
            'text': '🇨🇳 CPI/PPI数据'
        })

    # 工业/零售/固投 - 每月15日前后（遇周末顺延到下一工作日）
    ind_day = 15
    if is_weekend(year, month, ind_day):
        ind_day += 1
        while is_weekend(year, month, ind_day):
            ind_day += 1
    if ind_day <= days_in_month:
        events.append({
            'day': ind_day,
            'cls': 'macro',
            'text': '🇨🇳 工业增加值/社零/固投数据'
        })

    # LPR - 每月20日
    if 20 <= days_in_month:
        events.append({
            'day': 20,
            'cls': 'cbank',
            'text': '🇨🇳 LPR利率报价（1年期/5年期以上）'
        })

    # 中金所股指期货交割 - 每月第三个周五
    if not is_weekend(year, month, third_fri):
        is_holiday = False
        for market in HOLIDAYS_2026.get('cn', {}).get(month, []):
            if market == third_fri:
                is_holiday = True
        for market in HOLIDAYS_2027.get('cn', {}).get(month, []):
            if market == third_fri:
                is_holiday = True
        if not is_holiday:
            events.append({
                'day': third_fri,
                'cls': 'futures',
                'text': '📊 中金所IF/IH/IC/IM股指期货交割'
            })
            events.append({
                'day': third_fri,
                'cls': 'option',
                'text': '📊 中金所股指期权到期'
            })

    # A50交割 - 每月倒数第二个工作日
    a50_day = second_last
    events.append({
        'day': a50_day,
        'cls': 'a50',
        'text': '🇸🇬 富时A50交割日（每月倒数第二个工作日）'
    })

    # 各交易所期货/期权常规到期
    # 上期所/能源中心260X 最后交易日（约每月15日前后）
    # 大商所/郑商所/广期所 260X 最后交易日（约每月13-14日）
    # 这些根据不同月份月份码不同，简化处理

    return events


def get_earnings_deadlines(year, month):
    """财报截止日"""
    events = []
    if year == 2026 and month == 7:
        events.append({
            'day': 15,
            'cls': 'earn-end',
            'text': '🇨🇳 A股半年报预告强制截止日（净利润大幅变动/亏损企业须发布预告）'
        })
    if year == 2027 and month == 1:
        events.append({
            'day': 31,
            'cls': 'earn-end',
            'text': '🇨🇳 年报预告强制截止日'
        })
    if year == 2027 and month == 4:
        events.append({
            'day': 30,
            'cls': 'earn-end',
            'text': '🇨🇳 年报强制截止日'
        })
    if year == 2027 and month == 7:
        events.append({
            'day': 15,
            'cls': 'earn-end',
            'text': '🇨🇳 A股半年报预告强制截止日'
        })
    if year == 2027 and month == 10:
        events.append({
            'day': 31,
            'cls': 'earn-end',
            'text': '🇨🇳 三季报强制截止日'
        })
    return events


def get_apple_huawei_events(year, month):
    """苹果/华为发布活动"""
    events = []
    # 苹果Q1财报 - 1月最后一周
    if month == 1:
        events.append({
            'day': 28,
            'cls': 'phone',
            'text': '📱 苹果Q1财报（通常在1月最后一周）'
        })
    # 苹果Q2财报 - 4月最后一周
    if month == 4:
        events.append({
            'day': 29,
            'cls': 'phone',
            'text': '📱 苹果Q2财报'
        })
    # 苹果Q3财报 - 7月最后一周
    if month == 7:
        events.append({
            'day': 30,
            'cls': 'phone',
            'text': '📱 苹果Q3财报'
        })
    # 苹果Q4财报 - 10月最后一周
    if month == 10:
        events.append({
            'day': 28,
            'cls': 'phone',
            'text': '📱 苹果Q4财报'
        })
    # 华为新品 - 3月
    if month == 3:
        events.append({
            'day': 20,
            'cls': 'phone',
            'text': '📱 华为春季新品发布会（预计）'
        })
    # 华为新品 - 9月
    if month == 9:
        events.append({
            'day': 15,
            'cls': 'phone',
            'text': '📱 华为秋季新品发布会（预计）'
        })
    # 华为新品 - 11月
    if month == 11:
        events.append({
            'day': 20,
            'cls': 'phone',
            'text': '📱 华为冬季新品发布会（预计）'
        })
    return events


def get_fomc_events(year, month):
    """FOMC事件"""
    events = []
    fomc_data = FOMC_2026 if year == 2026 else (FOMC_2027 if year == 2027 else {})
    if month in fomc_data:
        for start_day, decision_day, desc in fomc_data[month]:
            days_in_month = calendar.monthrange(year, month)[1]
            if start_day <= days_in_month:
                events.append({
                    'day': start_day,
                    'cls': 'fomc',
                    'text': f'🇺🇸 FOMC议息会议第1天（{start_day}日）'
                })
            if decision_day != start_day and decision_day <= days_in_month:
                events.append({
                    'day': decision_day,
                    'cls': 'fomc',
                    'text': f'🇺🇸 {desc}'
                })
    return events


def get_holidays(year, month, market_type, prefix, cls_name, note_tentative=False):
    """获取假期事件"""
    events = []
    data = HOLIDAYS_2026 if year == 2026 else (HOLIDAYS_2027 if year == 2027 else {})
    if month in data.get(market_type, {}):
        for day in data[market_type][month]:
            # 为不同市场生成更具体的描述
            if market_type == 'hk':
                text = f'{prefix}休市'
            elif market_type == 'us':
                text = f'{prefix}休市'
            elif market_type == 'sg':
                text = f'{prefix}公假'
            elif market_type == 'cn':
                text = f'{prefix}休市'
                if note_tentative:
                    text += '（暂定，待官方公布确认）'
            events.append({
                'day': day,
                'cls': cls_name,
                'text': text
            })
    return events


def get_specific_2026_events(year, month):
    """2026年特定事件"""
    events = []

    if year == 2026:
        if month == 6:
            # 6月已过，但保留关键事件
            events.append({'day': 18, 'cls': 'fomc', 'text': '🇺🇸 FOMC利率决议+点阵图/SEP 北京时间6/18凌晨 ✅ 已发布'})
            events.append({'day': 19, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（端午节）'})
            events.append({'day': 19, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（六月节）'})
            events.append({'day': 19, 'cls': 'policy', 'text': '🇨🇳 A股端午休市（6/19-21）'})
            events.append({'day': 30, 'cls': 'macro', 'text': '🇨🇳 6月官方PMI ✅ 已发布'})

        elif month == 7:
            events.append({'day': 1, 'cls': 'policy', 'text': '🇨🇳 《社会救助法》等新规实施'})
            events.append({'day': 1, 'cls': 'macro', 'text': '🇨🇳 6月财新制造业PMI ✅ 已发布'})
            events.append({'day': 3, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（独立日补假）'})
            events.append({'day': 7, 'cls': 'option', 'text': '📊 广期所2608期权到期'})
            events.append({'day': 7, 'cls': 'kr-stock', 'text': '🇰🇷 三星电子Q2业绩指引'})
            events.append({'day': 8, 'cls': 'policy', 'text': '🇨🇳 国务院科技奖励决定'})
            events.append({'day': 9, 'cls': 'macro', 'text': '🇨🇳 6月CPI/PPI ✅ 已发布'})
            events.append({'day': 9, 'cls': 'fomc', 'text': '🇺🇸 美联储6月会议纪要 ✅ 已发布'})
            events.append({'day': 9, 'cls': 'us-stock', 'text': 'PEP 百事 盘前'})
            events.append({'day': 10, 'cls': 'us-stock', 'text': 'DAL 达美航空 盘前'})
            events.append({'day': 13, 'cls': 'option', 'text': '📊 郑商所2608期权到期'})
            events.append({'day': 14, 'cls': 'futures', 'text': '📊 大商所/郑商所/广期所2607最后交易日'})
            events.append({'day': 14, 'cls': 'phone', 'text': '📱 华为Pura 90S系列吉隆坡发布'})
            events.append({'day': 14, 'cls': 'us-stock', 'text': 'JPM 摩根大通 盘前'})
            events.append({'day': 14, 'cls': 'macro', 'text': '🇺🇸 6月CPI 20:30'})
            events.append({'day': 15, 'cls': 'futures', 'text': '📊 上期所/能源中心2607最后交易日'})
            events.append({'day': 15, 'cls': 'option', 'text': '📊 能源中心SC2608期权到期'})
            events.append({'day': 15, 'cls': 'us-stock', 'text': 'MS 摩根士丹利 盘前'})
            events.append({'day': 15, 'cls': 'us-stock', 'text': 'BLK 贝莱德 盘前'})
            events.append({'day': 16, 'cls': 'option', 'text': '📊 大商所2608期权到期'})
            events.append({'day': 16, 'cls': 'us-stock', 'text': 'NFLX 奈飞 盘后'})
            events.append({'day': 20, 'cls': 'option', 'text': '📊 上期所FU2608期权到期'})
            events.append({'day': 22, 'cls': 'us-stock', 'text': 'TSLA 特斯拉 盘后'})
            events.append({'day': 23, 'cls': 'us-stock', 'text': 'GOOGL 谷歌 盘后'})
            events.append({'day': 23, 'cls': 'eu-earn', 'text': '🇬🇧 EasyJet Q3交易更新'})
            events.append({'day': 27, 'cls': 'option', 'text': '📊 上期所2608期权到期（除FU）'})
            events.append({'day': 27, 'cls': 'option', 'text': '📊 能源中心NR/BC2608期权到期'})
            events.append({'day': 27, 'cls': 'futures', 'text': '📊 能源中心EC2607最后交易日'})
            events.append({'day': 28, 'cls': 'futures', 'text': '📊 大商所BZ/EB/EG/JD等2607最后交易日'})
            events.append({'day': 28, 'cls': 'us-stock', 'text': 'SPGI 标普全球 盘前'})
            events.append({'day': 29, 'cls': 'option', 'text': '📊 郑商所CJ/PX2609期权到期'})
            events.append({'day': 29, 'cls': 'us-stock', 'text': 'MSFT 微软 盘后'})
            events.append({'day': 29, 'cls': 'us-stock', 'text': 'META 盘后'})
            events.append({'day': 29, 'cls': 'us-stock', 'text': 'ADP 盘前'})
            events.append({'day': 29, 'cls': 'eu-earn', 'text': '🇬🇧 Rio Tinto（力拓）半年报'})
            events.append({'day': 30, 'cls': 'us-stock', 'text': 'AAPL 苹果 盘后（库克最后1次CEO身份）'})
            events.append({'day': 30, 'cls': 'us-stock', 'text': 'AMZN 亚马逊 盘后'})
            events.append({'day': 30, 'cls': 'eu-earn', 'text': '🇪🇺 Shell/Rolls-Royce等半年报'})
            events.append({'day': 30, 'cls': 'kr-stock', 'text': '🇰🇷 三星电子Q2完整财报'})
            events.append({'day': 31, 'cls': 'futures', 'text': '📊 上期所FU2608/能源中心SC/LU2608最后交易日'})

        elif month == 8:
            events.append({'day': 10, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（国庆补假）'})
            events.append({'day': 14, 'cls': 'futures', 'text': '📊 大商所/郑商所/广期所2608最后交易日'})
            events.append({'day': 17, 'cls': 'futures', 'text': '📊 上期所/能源中心2608最后交易日'})
            events.append({'day': 21, 'cls': 'option', 'text': '📊 中金所HO/IO/MO 2608股指期权到期'})
            events.append({'day': 28, 'cls': 'futures', 'text': '📊 能源中心EC2608最后交易日'})

        elif month == 9:
            events.append({'day': 7, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（劳动节）'})
            events.append({'day': 11, 'cls': 'futures', 'text': '📊 大商所/郑商所/广期所2609最后交易日'})
            events.append({'day': 14, 'cls': 'futures', 'text': '📊 上期所/能源中心2609最后交易日'})
            events.append({'day': 16, 'cls': 'fomc', 'text': '🇺🇸 FOMC议息会议第1天（9/16）'})
            events.append({'day': 17, 'cls': 'fomc', 'text': '🇺🇸 FOMC利率决议+点阵图/SEP 北京时间9/17凌晨'})
            events.append({'day': 25, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（中秋节翌日）'})
            events.append({'day': 25, 'cls': 'policy', 'text': '🇨🇳 A股中秋休市（9/25-27）'})

        elif month == 10:
            events.append({'day': 1, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（国庆日）'})
            events.append({'day': 1, 'cls': 'policy', 'text': '🇨🇳 A股国庆休市（10/1-7）'})
            events.append({'day': 14, 'cls': 'futures', 'text': '📊 大商所/郑商所/广期所2610最后交易日'})
            events.append({'day': 15, 'cls': 'futures', 'text': '📊 上期所/能源中心2610最后交易日'})
            events.append({'day': 19, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（重阳节）'})
            events.append({'day': 28, 'cls': 'fomc', 'text': '🇺🇸 FOMC议息会议第1天（10/28）'})
            events.append({'day': 29, 'cls': 'fomc', 'text': '🇺🇸 FOMC利率决议 北京时间10/29凌晨'})
            events.append({'day': 30, 'cls': 'futures', 'text': '📊 能源中心EC2610最后交易日'})

        elif month == 11:
            events.append({'day': 9, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（屠妖节补假）'})
            events.append({'day': 13, 'cls': 'futures', 'text': '📊 大商所/郑商所/广期所2611最后交易日'})
            events.append({'day': 16, 'cls': 'futures', 'text': '📊 上期所/能源中心2611最后交易日'})
            events.append({'day': 26, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（感恩节）'})
            events.append({'day': 27, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（感恩节翌日早收1pm）'})

        elif month == 12:
            events.append({'day': 9, 'cls': 'fomc', 'text': '🇺🇸 FOMC议息会议第1天（12/9）'})
            events.append({'day': 10, 'cls': 'fomc', 'text': '🇺🇸 FOMC利率决议+点阵图/SEP 北京时间12/10凌晨'})
            events.append({'day': 11, 'cls': 'futures', 'text': '📊 大商所/郑商所/广期所2612最后交易日'})
            events.append({'day': 14, 'cls': 'futures', 'text': '📊 上期所/能源中心2612最后交易日'})
            events.append({'day': 24, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（平安夜半日）'})
            events.append({'day': 24, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（圣诞前夕早收1pm）'})
            events.append({'day': 25, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（圣诞节）'})
            events.append({'day': 25, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（圣诞节）'})
            events.append({'day': 25, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（圣诞节）'})
            events.append({'day': 31, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（新年前夜半日）'})

    return events


def get_specific_2027_events(year, month):
    """2027年特定事件"""
    events = []

    if year == 2027:
        if month == 1:
            events.append({'day': 1, 'cls': 'policy', 'text': '🇨🇳 A股元旦休市（1/1-3）'})
            events.append({'day': 18, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（马丁路德金纪念日）'})

        elif month == 2:
            events.append({'day': 13, 'cls': 'policy', 'text': '🇨🇳 A股春节休市（约2/13-21，暂定）'})
            events.append({'day': 15, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（总统日）'})

        elif month == 3:
            events.append({'day': 10, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（开斋节）'})
            events.append({'day': 26, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（耶稣受难日）'})
            events.append({'day': 26, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（耶稣受难日）'})
            events.append({'day': 26, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（受难日）'})
            events.append({'day': 27, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（耶稣受难日翌日）'})
            events.append({'day': 29, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（复活节星期一）'})

        elif month == 4:
            events.append({'day': 4, 'cls': 'policy', 'text': '🇨🇳 A股清明休市（4/4-5，暂定）'})
            events.append({'day': 5, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（清明节）'})

        elif month == 5:
            events.append({'day': 1, 'cls': 'policy', 'text': '🇨🇳 A股劳动节休市（5/1-5）'})
            events.append({'day': 13, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（佛诞）'})
            events.append({'day': 17, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（哈芝节）'})
            events.append({'day': 20, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（卫塞节补假）'})
            events.append({'day': 31, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（阵亡将士纪念日）'})

        elif month == 6:
            events.append({'day': 9, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（端午节）'})
            events.append({'day': 12, 'cls': 'policy', 'text': '🇨🇳 A股端午休市（约6/12-14，暂定）'})
            events.append({'day': 18, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（六月节补假）'})

        elif month == 7:
            events.append({'day': 5, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（独立日补假）'})

        elif month == 8:
            events.append({'day': 9, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（国庆日）'})

        elif month == 9:
            events.append({'day': 6, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（劳动节）'})
            events.append({'day': 16, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（中秋节翌日）'})
            events.append({'day': 24, 'cls': 'policy', 'text': '🇨🇳 A股中秋休市（约9/24-26，暂定）'})

        elif month == 10:
            events.append({'day': 1, 'cls': 'policy', 'text': '🇨🇳 A股国庆休市（10/1-7）'})
            events.append({'day': 8, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（重阳节）'})
            events.append({'day': 28, 'cls': 'sg-holiday', 'text': '🇸🇬 SG公假（屠妖节）'})

        elif month == 11:
            events.append({'day': 25, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（感恩节）'})
            events.append({'day': 26, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（感恩节翌日早收）'})

        elif month == 12:
            events.append({'day': 24, 'cls': 'us-holiday', 'text': '🇺🇸 美股休市（圣诞前夕早收）'})
            events.append({'day': 27, 'cls': 'hk-holiday', 'text': '🇭🇰 港股休市（圣诞节补假）'})

    return events


# ==================== 图例/色块标注区定义 ====================

def get_legend_blocks(year, month):
    """获取底部色块标注区内容"""
    # 根据月份和年份动态生成图例说明
    month_name = f"{year}年{month}月"

    blocks = [
        {
            'color': '#ff8c42',
            'title': '重要政策',
            'detail': '中国政府/监管机构发布的重大政策、法规、规划等',
            'items': [
                '暂无数据，待政策发布后补充'
            ]
        },
        {
            'color': '#ffd700',
            'title': '央行/LPR',
            'detail': '央行货币政策、利率决议、LPR报价等',
            'items': [
                f'{month}月 LPR利率报价（1年期/5年期以上）— 每月20日'
            ]
        },
        {
            'color': '#e8b830',
            'title': '中国数据',
            'detail': '国家统计局/央行发布的宏观经济数据',
            'items': [
                f'CPI/PPI — 每月9日前后',
                f'工业增加值/社零/固投 — 每月15日前后',
                f'PMI — 每月最后一日',
            ]
        },
        {
            'color': '#ff6b55',
            'title': '财报截止',
            'detail': 'A股财报披露法定截止日',
            'items': [
                get_earn_deadline_text(year, month)
            ]
        },
        {
            'color': '#4da6ff',
            'title': '期权交割',
            'detail': '各交易所期权合约到期日，涉及多空博弈',
            'items': [
                '中金所股指期权 — 每月第三个周五',
                '各商品交易所期权到期日 — 每月约13-20日间',
                '暂无具体数据，待各交易所公告后补充'
            ]
        },
        {
            'color': '#6cb4ff',
            'title': '期货交割',
            'detail': '期货合约最后交易日，交割前后波动加大',
            'items': [
                '中金所IF/IH/IC/IM股指期货 — 每月第三个周五',
                f'各交易所{year%100}{month:02d}合约最后交易日 — 约每月中旬',
            ]
        },
        {
            'color': '#ff5a52',
            'title': 'A50交割',
            'detail': '新加坡富时A50期货交割日，外资对冲A股关键窗口',
            'items': [
                f'{month_name} — 倒数第二个工作日',
                '⚠️ 交割前1-2周可能出现提前移仓波动'
            ]
        },
        {
            'color': '#78828a',
            'title': '港股休市',
            'detail': '香港交易所休市日，港股通/沪深股通同步暂停',
            'items': get_hk_holiday_items(year, month)
        },
        {
            'color': '#2ea043',
            'title': '台股财报',
            'detail': '台股关注股票',
            'items': [
                '台积电TSMC',
                '联发科MediaTek'
            ]
        },
        {
            'color': '#c084fc',
            'title': '苹果/华为发布',
            'detail': '两大科技巨头新品发布/重要财报',
            'items': get_phone_items(year, month)
        },
        {
            'color': '#e63946',
            'title': 'FOMC',
            'detail': '美联储货币政策会议，全球资本市场核心事件',
            'items': get_fomc_items(year, month)
        },
        {
            'color': '#5a6270',
            'title': '美股休市',
            'detail': 'NYSE/Nasdaq休市日',
            'items': get_us_holiday_items(year, month)
        },
        {
            'color': '#388bfd',
            'title': '美股财报',
            'detail': '美股关注股票',
            'items': [
                '特斯拉TSLA',
                '谷歌GOOGL',
                '微软MSFT',
                '苹果AAPL',
                '亚马逊AMZN',
                'Meta',
                '英伟达NVDA',
                '美光MU'
            ]
        },
        {
            'color': '#4ade80',
            'title': '欧股财报',
            'detail': '欧股关注股票',
            'items': [
                'ASML阿斯麦'
            ]
        },
        {
            'color': '#f0c040',
            'title': '日股财报',
            'detail': '日股关注股票',
            'items': [
                '铠侠Kioxia',
                '东京电子',
                '爱德万测试'
            ]
        },
        {
            'color': '#a855f7',
            'title': '韩股财报',
            'detail': '韩股关注股票',
            'items': [
                '三星电子',
                'SK海力士'
            ]
        },
        {
            'color': '#ff528a',
            'title': 'SG公假',
            'detail': '新加坡公共假期',
            'items': get_sg_holiday_items(year, month)
        },
    ]
    return blocks


def get_earn_deadline_text(year, month):
    if year == 2026:
        if month == 7:
            return '7/15 🇨🇳 A股半年报预告强制截止日'
        elif month == 1:
            return '1/31 🇨🇳 年报预告强制截止日'
        elif month == 4:
            return '4/30 🇨🇳 年报强制截止日'
        elif month == 10:
            return '10/31 🇨🇳 三季报强制截止日'
    if year == 2027:
        if month == 1:
            return '1/31 🇨🇳 年报预告强制截止日'
        elif month == 4:
            return '4/30 🇨🇳 年报强制截止日'
        elif month == 7:
            return '7/15 🇨🇳 A股半年报预告强制截止日'
        elif month == 10:
            return '10/31 🇨🇳 三季报强制截止日'
    return '本月无法规截止日'


def get_phone_items(year, month):
    items = []
    data = HOLIDAYS_2026 if year == 2026 else (HOLIDAYS_2027 if year == 2027 else {})
    if month == 1:
        items.append('苹果Q1财报 — 1月最后一周')
    elif month == 4:
        items.append('苹果Q2财报 — 4月最后一周')
    elif month == 7:
        items.append('苹果Q3财报 — 7月最后一周')
    elif month == 10:
        items.append('苹果Q4财报 — 10月最后一周')
    if month == 3:
        items.append('华为春季新品发布会 — 3月（预计）')
    elif month == 9:
        items.append('华为秋季新品发布会 — 9月（预计）')
    elif month == 11:
        items.append('华为冬季新品发布会 — 11月（预计）')
    if not items:
        items.append('暂无数据，待各公司公布具体日期后补充')
    return items


def get_fomc_items(year, month):
    items = []
    fomc_data = FOMC_2026 if year == 2026 else (FOMC_2027 if year == 2027 else {})
    if month in fomc_data:
        for start_day, decision_day, desc in fomc_data[month]:
            items.append(f'{month}/{start_day}-{decision_day} {desc}')
    if not items:
        items.append('本月无FOMC会议')
    return items


def get_hk_holiday_items(year, month):
    data = HOLIDAYS_2026 if year == 2026 else (HOLIDAYS_2027 if year == 2027 else {})
    items = []
    if month in data.get('hk', {}):
        for day in data['hk'][month]:
            items.append(f'{month}/{day} 🇭🇰 港股休市')
    if not items:
        items.append('本月港股无休市')
    return items


def get_us_holiday_items(year, month):
    data = HOLIDAYS_2026 if year == 2026 else (HOLIDAYS_2027 if year == 2027 else {})
    items = []
    if month in data.get('us', {}):
        for day in data['us'][month]:
            items.append(f'{month}/{day} 🇺🇸 美股休市')
    if not items:
        items.append('本月美股无休市')
    return items


def get_sg_holiday_items(year, month):
    data = HOLIDAYS_2026 if year == 2026 else (HOLIDAYS_2027 if year == 2027 else {})
    items = []
    if month in data.get('sg', {}):
        for day in data['sg'][month]:
            items.append(f'{month}/{day} 🇸🇬 SG公假')
    if not items:
        items.append('本月新加坡无公共假期')
    return items


# ==================== HTML生成 ====================

def generate_month_html(year, month, today_date=None):
    """生成单月日历HTML"""
    if today_date is None:
        today_date = date.today()

    month_name = f"{year}年{month}月"
    # 计算上下月导航
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    prev_label = f"{prev_y}年{prev_m}月"
    next_label = f"{next_y}年{next_m}月"
    prev_file = f"重要日历_{prev_y}{prev_m:02d}.html"
    next_file = f"重要日历_{next_y}{next_m:02d}.html"
    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday = date(year, month, 1).weekday()  # 0=周一

    # 收集所有事件
    all_events = []
    all_events.extend(get_recurring_events(year, month))
    all_events.extend(get_earnings_deadlines(year, month))
    all_events.extend(get_apple_huawei_events(year, month))
    all_events.extend(get_fomc_events(year, month))
    # 假期
    all_events.extend(get_holidays(year, month, 'hk', '🇭🇰 港股', 'hk-holiday'))
    all_events.extend(get_holidays(year, month, 'us', '🇺🇸 美股', 'us-holiday'))
    all_events.extend(get_holidays(year, month, 'sg', '🇸🇬 SG', 'sg-holiday'))
    cn_tentative = (year == 2027)
    all_events.extend(get_holidays(year, month, 'cn', '🇨🇳 A股', 'policy', cn_tentative))
    # 特定年份事件
    all_events.extend(get_specific_2026_events(year, month))
    all_events.extend(get_specific_2027_events(year, month))

    # 去重（同一天、同类、同文本的只保留一个）
    seen = set()
    deduped = []
    for ev in all_events:
        key = (ev['day'], ev['cls'], ev['text'])
        if key not in seen:
            seen.add(key)
            deduped.append(ev)
    all_events = deduped

    # 按日期分组
    events_by_day = {}
    for ev in all_events:
        day = ev['day']
        if day not in events_by_day:
            events_by_day[day] = []
        events_by_day[day].append(ev)

    # 获取上个月最后几天的日期
    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1
    prev_days_in_month = calendar.monthrange(prev_year, prev_month)[1]

    # 获取下个月前几天的日期
    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1

    # 计算日历网格
    # first_weekday: 0=周一, 6=周日
    # 日历从周一开始
    start_offset = first_weekday  # 前面空白格数
    total_cells = start_offset + days_in_month
    # 补全到7的倍数
    remainder = total_cells % 7
    if remainder > 0:
        total_cells += 7 - remainder

    num_weeks = total_cells // 7

    # 构建HTML
    lines = []
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="zh-CN">')
    lines.append('<head>')
    lines.append('    <meta charset="UTF-8">')
    lines.append('    <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append(f'    <title>重要日历 - {month_name}</title>')
    lines.append('    <style>')
    lines.append('        * { margin: 0; padding: 0; box-sizing: border-box; }')
    lines.append('        body {')
    lines.append('            font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', \'PingFang SC\', \'Microsoft YaHei\', sans-serif;')
    lines.append('            background: #0d1117;')
    lines.append('            min-height: 100vh;')
    lines.append('            padding: 20px;')
    lines.append('            color: #c9d1d9;')
    lines.append('        }')
    lines.append('        .container {')
    lines.append('            max-width: 1500px;')
    lines.append('            margin: 0 auto;')
    lines.append('            background: #161b22;')
    lines.append('            border-radius: 12px;')
    lines.append('            border: 1px solid #30363d;')
    lines.append('            padding: 30px;')
    lines.append('        }')
    lines.append('        .header {')
    lines.append('            text-align: center;')
    lines.append('            padding: 20px 0;')
    lines.append('            border-bottom: 1px solid #30363d;')
    lines.append('            margin-bottom: 20px;')
    lines.append('        }')
    lines.append('        .nav-bar {')
    lines.append('            display: flex;')
    lines.append('            align-items: center;')
    lines.append('            justify-content: space-between;')
    lines.append('            padding: 0 10px;')
    lines.append('        }')
    lines.append('        .nav-arrow {')
    lines.append('            color: #8b949e;')
    lines.append('            text-decoration: none;')
    lines.append('            font-size: 15px;')
    lines.append('            font-weight: 500;')
    lines.append('            padding: 8px 16px;')
    lines.append('            border-radius: 6px;')
    lines.append('            transition: background 0.2s;')
    lines.append('        }')
    lines.append('        .nav-arrow:hover {')
    lines.append('            background: #21262d;')
    lines.append('            color: #58a6ff;')
    lines.append('        }')
    lines.append('        .nav-title {')
    lines.append('            text-align: center;')
    lines.append('        }')
    lines.append('        .nav-title h1 {')
    lines.append('            font-size: 24px;')
    lines.append('            font-weight: 600;')
    lines.append('            color: #58a6ff;')
    lines.append('            margin-bottom: 4px;')
    lines.append('        }')
    lines.append('        .nav-current {')
    lines.append('            color: #c9d1d9;')
    lines.append('            font-size: 14px;')
    lines.append('            font-weight: 500;')
    lines.append('        }')
    lines.append('        .nav-current .highlight {')
    lines.append('            color: #58a6ff;')
    lines.append('        }')
    lines.append('        .update-info {')
    lines.append('            text-align: center;')
    lines.append('            margin-top: 8px;')
    lines.append('            font-size: 12px;')
    lines.append('            color: #6e7681;')
    lines.append('        }')
    lines.append('')
    lines.append('        /* 分类标签导航栏 */')
    lines.append('        .category-nav {')
    lines.append('            display: flex;')
    lines.append('            flex-wrap: wrap;')
    lines.append('            gap: 6px;')
    lines.append('            margin-bottom: 20px;')
    lines.append('            padding: 10px;')
    lines.append('            background: #0d1117;')
    lines.append('            border-radius: 8px;')
    lines.append('            border: 1px solid #30363d;')
    lines.append('        }')
    lines.append('        .category-tag {')
    lines.append('            padding: 4px 10px;')
    lines.append('            border-radius: 4px;')
    lines.append('            font-size: 11px;')
    lines.append('            font-weight: 500;')
    lines.append('            white-space: nowrap;')
    lines.append('        }')
    lines.append('        .category-tag.today { background: #d29922; color: #fff; }')
    lines.append('        .category-tag.policy { background: #ff8c42; color: #fff; }')
    lines.append('        .category-tag.cbank { background: #ffd700; color: #fff; }')
    lines.append('        .category-tag.cndata { background: #e8b830; color: #fff; }')
    lines.append('        .category-tag.earn-end { background: #ff6b55; color: #fff; }')
    lines.append('        .category-tag.option { background: #4da6ff; color: #fff; }')
    lines.append('        .category-tag.futures { background: #6cb4ff; color: #fff; }')
    lines.append('        .category-tag.a50 { background: #ff5a52; color: #fff; }')
    lines.append('        .category-tag.hk-holiday { background: #78828a; color: #fff; }')
    lines.append('        .category-tag.tw { background: #2ea043; color: #fff; }')
    lines.append('        .category-tag.phone { background: #c084fc; color: #fff; }')
    lines.append('        .category-tag.fomc { background: #e63946; color: #fff; }')
    lines.append('        .category-tag.us-holiday { background: #5a6270; color: #fff; }')
    lines.append('        .category-tag.us-earn { background: #388bfd; color: #fff; }')
    lines.append('        .category-tag.eu-earn { background: #4ade80; color: #fff; }')
    lines.append('        .category-tag.jp-earn { background: #f0c040; color: #fff; }')
    lines.append('        .category-tag.kr-earn { background: #a855f7; color: #fff; }')
    lines.append('        .category-tag.sg-holiday { background: #ff528a; color: #fff; }')
    lines.append('')
    lines.append('        /* 周列表 */')
    lines.append('        .week-table {')
    lines.append('            width: 100%;')
    lines.append('            border-collapse: collapse;')
    lines.append('            margin-bottom: 20px;')
    lines.append('        }')
    lines.append('        .week-table thead th {')
    lines.append('            background: #21262d;')
    lines.append('            color: #c9d1d9;')
    lines.append('            padding: 10px 6px;')
    lines.append('            font-size: 13px;')
    lines.append('            font-weight: 500;')
    lines.append('            text-align: center;')
    lines.append('            border: 1px solid #30363d;')
    lines.append('        }')
    lines.append('        .week-table tbody td {')
    lines.append('            border: 1px solid #30363d;')
    lines.append('            vertical-align: top;')
    lines.append('            padding: 6px;')
    lines.append('            background: #0d1117;')
    lines.append('            width: 14.28%;')
    lines.append('        }')
    lines.append('        .week-table tbody td.weekend {')
    lines.append('            background: #161b22;')
    lines.append('        }')
    lines.append('        .week-table tbody td.other-month {')
    lines.append('            opacity: 0.4;')
    lines.append('        }')
    lines.append('')
    lines.append('        .day-cell {')
    lines.append('            display: flex;')
    lines.append('            flex-direction: column;')
    lines.append('            min-height: 180px;')
    lines.append('        }')
    lines.append('        .day-cell .day-header {')
    lines.append('            display: flex;')
    lines.append('            justify-content: space-between;')
    lines.append('            align-items: center;')
    lines.append('            margin-bottom: 4px;')
    lines.append('            flex-shrink: 0;')
    lines.append('        }')
    lines.append('        .day-cell .day-number {')
    lines.append('            font-size: 13px;')
    lines.append('            font-weight: 600;')
    lines.append('            color: #c9d1d9;')
    lines.append('            width: 22px;')
    lines.append('            height: 22px;')
    lines.append('            display: flex;')
    lines.append('            align-items: center;')
    lines.append('            justify-content: center;')
    lines.append('            border-radius: 50%;')
    lines.append('        }')
    lines.append('        .day-cell .day-number.today {')
    lines.append('            background: #58a6ff;')
    lines.append('            color: #fff;')
    lines.append('        }')
    lines.append('        .day-cell .day-number.other {')
    lines.append('            color: #6e7681;')
    lines.append('        }')
    lines.append('')
    lines.append('        .day-cell .event-list {')
    lines.append('            font-size: 10.5px;')
    lines.append('            line-height: 1.35;')
    lines.append('            flex: 1;')
    lines.append('            overflow-y: auto;')
    lines.append('        }')
    lines.append('        .day-cell .event-item {')
    lines.append('            padding: 2px 4px;')
    lines.append('            margin-bottom: 2px;')
    lines.append('            border-radius: 3px;')
    lines.append('            font-size: 10.5px;')
    lines.append('            line-height: 1.3;')
    lines.append('        }')
    lines.append('')
    lines.append('        .event-item.policy { background: #ff8c42; border-left: 3px solid #ff8c42; color: #fff; }')
    lines.append('        .event-item.cbank { background: #ffd700; border-left: 3px solid #ffd700; color: #fff; }')
    lines.append('        .event-item.macro { background: #e8b830; border-left: 3px solid #e8b830; color: #fff; }')
    lines.append('        .event-item.earn-end { background: #ff6b55; border-left: 3px solid #ff6b55; color: #fff; }')
    lines.append('        .event-item.option { background: #4da6ff; border-left: 3px solid #4da6ff; color: #fff; }')
    lines.append('        .event-item.futures { background: #6cb4ff; border-left: 3px solid #6cb4ff; color: #fff; }')
    lines.append('        .event-item.a50 { background: #ff5a52; border-left: 3px solid #ff5a52; color: #fff; }')
    lines.append('        .event-item.hk-holiday { background: #78828a; border-left: 3px solid #78828a; color: #fff; }')
    lines.append('        .event-item.tw-stock { background: #2ea043; border-left: 3px solid #2ea043; color: #fff; }')
    lines.append('        .event-item.phone { background: #c084fc; border-left: 3px solid #c084fc; color: #fff; }')
    lines.append('        .event-item.fomc { background: #e63946; border-left: 3px solid #e63946; color: #fff; }')
    lines.append('        .event-item.us-holiday { background: #5a6270; border-left: 3px solid #5a6270; color: #fff; }')
    lines.append('        .event-item.us-stock { background: #388bfd; border-left: 3px solid #388bfd; color: #fff; }')
    lines.append('        .event-item.eu-earn { background: #4ade80; border-left: 3px solid #4ade80; color: #fff; }')
    lines.append('        .event-item.jp-earn { background: #f0c040; border-left: 3px solid #f0c040; color: #fff; }')
    lines.append('        .event-item.kr-stock { background: #a855f7; border-left: 3px solid #a855f7; color: #fff; }')
    lines.append('        .event-item.sg-holiday { background: #ff528a; border-left: 3px solid #ff528a; color: #fff; }')
    lines.append('')
    lines.append('        .day-cell .empty-content {')
    lines.append('            flex: 1;')
    lines.append('            display: flex;')
    lines.append('            align-items: center;')
    lines.append('            justify-content: center;')
    lines.append('            color: #6e7681;')
    lines.append('            font-size: 11px;')
    lines.append('        }')
    lines.append('')
    lines.append('        /* 色块图例 */')
    lines.append('        .legend-section {')
    lines.append('            margin-top: 30px;')
    lines.append('            border-top: 1px solid #30363d;')
    lines.append('            padding-top: 25px;')
    lines.append('        }')
    lines.append('        .legend-section h3 {')
    lines.append('            font-size: 18px;')
    lines.append('            margin-bottom: 20px;')
    lines.append('            color: #c9d1d9;')
    lines.append('        }')
    lines.append('        .legend-grid {')
    lines.append('            display: grid;')
    lines.append('            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));')
    lines.append('            gap: 12px;')
    lines.append('        }')
    lines.append('        .legend-block {')
    lines.append('            background: #21262d;')
    lines.append('            border-radius: 8px;')
    lines.append('            padding: 14px 16px;')
    lines.append('            border: 1px solid #30363d;')
    lines.append('        }')
    lines.append('        .legend-block .legend-header {')
    lines.append('            display: flex;')
    lines.append('            align-items: center;')
    lines.append('            gap: 8px;')
    lines.append('            margin-bottom: 8px;')
    lines.append('            font-size: 13px;')
    lines.append('            font-weight: 600;')
    lines.append('        }')
    lines.append('        .legend-block .legend-header .color-dot {')
    lines.append('            width: 12px;')
    lines.append('            height: 12px;')
    lines.append('            border-radius: 3px;')
    lines.append('            flex-shrink: 0;')
    lines.append('        }')
    lines.append('        .legend-block .legend-detail {')
    lines.append('            font-size: 11.5px;')
    lines.append('            line-height: 1.6;')
    lines.append('            color: #8b949e;')
    lines.append('        }')
    lines.append('        .legend-block .legend-detail ul {')
    lines.append('            list-style: none;')
    lines.append('            padding: 0;')
    lines.append('            margin: 4px 0 0 0;')
    lines.append('        }')
    lines.append('        .legend-block .legend-detail ul li {')
    lines.append('            padding: 1px 0;')
    lines.append('        }')
    lines.append('        .legend-block .legend-detail ul li::before {')
    lines.append('            content: "· ";')
    lines.append('        }')
    lines.append('')
    lines.append('        .footer-note {')
    lines.append('            margin-top: 25px;')
    lines.append('            padding: 20px;')
    lines.append('            background: #21262d;')
    lines.append('            border-radius: 8px;')
    lines.append('            border: 1px solid #30363d;')
    lines.append('            font-size: 12px;')
    lines.append('            color: #8b949e;')
    lines.append('            line-height: 1.6;')
    lines.append('        }')
    lines.append('        .footer-note strong { color: #c9d1d9; }')
    lines.append('')
    lines.append('        .update-time {')
    lines.append('            text-align: center;')
    lines.append('            margin-top: 20px;')
    lines.append('            font-size: 12px;')
    lines.append('            color: #6e7681;')
    lines.append('        }')
    lines.append('')
    lines.append('        @media (max-width: 768px) {')
    lines.append('            .container { padding: 10px; }')
    lines.append('            .week-table tbody td { min-height: 120px; padding: 4px; }')
    lines.append('            .day-cell { min-height: 120px; }')
    lines.append('            .day-cell .event-list { font-size: 9px; }')
    lines.append('            .day-cell .event-item { font-size: 9px; }')
    lines.append('            .legend-grid { grid-template-columns: 1fr; }')
    lines.append('        }')
    lines.append('    </style>')
    lines.append('</head>')
    lines.append('<body>')
    lines.append('<div class="container">')
    lines.append('    <div class="header">')
    lines.append('        <div class="nav-bar" style="display:flex;align-items:center;justify-content:space-between;gap:12px;">')
    lines.append(f'            <a href="portal.html" style="display:inline-flex;align-items:center;gap:6px;padding:8px 18px;background:linear-gradient(135deg,#c9b1ff,#d7c4ff);border-radius:20px;color:#fff;font-size:13px;font-weight:600;text-decoration:none;transition:all 0.2s;box-shadow:0 2px 8px rgba(201,177,255,0.4);letter-spacing:0.5px;flex-shrink:0;">📅 返回九宝日历精选</a>')
    lines.append(f'            <div style="text-align:center;flex:1;">')
    lines.append('                <h1 style="margin:0;font-size:20px;">重要日历</h1>')
    lines.append(f'                <div class="nav-current"><span class="highlight">{month_name}</span> · 全市场重要事件一览</div>')
    lines.append('            </div>')
    lines.append(f'            <div style="display:flex;align-items:center;gap:12px;flex-shrink:0;">')
    lines.append(f'                <a href="{prev_file}" class="nav-arrow">← {prev_label}</a>')
    lines.append(f'                <a href="{next_file}" class="nav-arrow">{next_label} →</a>')
    lines.append('            </div>')
    lines.append('        </div>')
    lines.append(f'        <div class="update-info">数据涵盖A股/港股/美股/欧股/日韩台/新加坡 | 每月1日更新 | 本次更新时间: {today_date.strftime("%Y-%m-%d %H:%M")}</div>')
    lines.append('    </div>')
    lines.append('')
    lines.append('    <!-- 分类标签导航 -->')
    lines.append('    <div class="category-nav">')
    lines.append('        <span class="category-tag today">今天</span>')
    lines.append('        <span class="category-tag policy">重要政策</span>')
    lines.append('        <span class="category-tag cbank">央行/LPR</span>')
    lines.append('        <span class="category-tag cndata">中国数据</span>')
    lines.append('        <span class="category-tag earn-end">财报截止</span>')
    lines.append('        <span class="category-tag option">期权交割</span>')
    lines.append('        <span class="category-tag futures">期货交割</span>')
    lines.append('        <span class="category-tag a50">A50交割</span>')
    lines.append('        <span class="category-tag hk-holiday">港股休市</span>')
    lines.append('        <span class="category-tag tw">台股财报</span>')
    lines.append('        <span class="category-tag phone">苹果/华为发布</span>')
    lines.append('        <span class="category-tag fomc">FOMC</span>')
    lines.append('        <span class="category-tag us-holiday">美股休市</span>')
    lines.append('        <span class="category-tag us-earn">美股财报</span>')
    lines.append('        <span class="category-tag eu-earn">欧股财报</span>')
    lines.append('        <span class="category-tag jp-earn">日股财报</span>')
    lines.append('        <span class="category-tag kr-earn">韩股财报</span>')
    lines.append('        <span class="category-tag sg-holiday">SG公假</span>')
    lines.append('    </div>')
    lines.append('')

    # 生成每周表格
    for week_idx in range(num_weeks):
        lines.append(f'    <!-- 第{week_idx + 1}周 -->')
        lines.append('    <table class="week-table">')
        lines.append('        <thead>')
        lines.append('            <tr>')
        lines.append('                <th>周一</th><th>周二</th><th>周三</th><th>周四</th><th>周五</th><th>周六</th><th>周日</th>')
        lines.append('            </tr>')
        lines.append('        </thead>')
        lines.append('        <tbody>')
        lines.append('            <tr>')

        for col in range(7):
            cell_idx = week_idx * 7 + col
            actual_day = cell_idx - start_offset + 1

            is_current_month = 1 <= actual_day <= days_in_month
            is_other_month = not is_current_month
            is_weekend_day = col >= 5  # 周六=5, 周日=6

            td_class = ''
            if is_other_month:
                td_class = 'other-month'
            elif is_weekend_day:
                td_class = 'weekend'

            lines.append(f'                <td{" class=\"" + td_class + "\"" if td_class else ""}>')

            if is_current_month:
                day_num = actual_day
                is_today = (year == today_date.year and month == today_date.month and day_num == today_date.day)
                day_events = events_by_day.get(day_num, [])

                lines.append('                    <div class="day-cell">')
                lines.append('                        <div class="day-header">')
                today_class = 'today' if is_today else ''
                lines.append(f'                            <span class="day-number{" " + today_class if today_class else ""}">{day_num}</span>')
                lines.append('                        </div>')

                if day_events:
                    lines.append('                        <div class="event-list">')
                    for ev in sorted(day_events, key=lambda x: x['cls']):
                        lines.append(f'                            <div class="event-item {ev["cls"]}">{ev["text"]}</div>')
                    lines.append('                        </div>')
                else:
                    lines.append('                        <div class="empty-content">--</div>')

                lines.append('                    </div>')
            else:
                # 其他月份日期
                if actual_day < 1:
                    other_day = prev_days_in_month + actual_day
                else:
                    other_day = actual_day - days_in_month
                lines.append('                    <div class="day-cell">')
                lines.append('                        <div class="day-header">')
                lines.append(f'                            <span class="day-number other">{other_day}</span>')
                lines.append('                        </div>')
                if is_weekend_day and is_other_month:
                    lines.append('                        <div class="empty-content"><span style="color:#6e7681;">休市</span></div>')
                else:
                    lines.append('                        <div class="empty-content">--</div>')
                lines.append('                    </div>')

            lines.append('                </td>')

        lines.append('            </tr>')
        lines.append('        </tbody>')
        lines.append('    </table>')

        # 周标签
        week_label = get_week_label(week_idx, days_in_month, year, month)
        # 检查是否有重要事件
        warning_notes = []
        for day_num in range(1, days_in_month + 1):
            day_events = events_by_day.get(day_num, [])
            for ev in day_events:
                if ev['cls'] in ('fomc', 'earn-end', 'a50'):
                    note = f'{day_num}日'
                    if ev['cls'] == 'fomc':
                        note += 'FOMC决议'
                    elif ev['cls'] == 'earn-end':
                        note += '财报截止'
                    elif ev['cls'] == 'a50':
                        note += 'A50交割'
                    warning_notes.append(note)

        week_warning = ''
        first_day_of_week = 1 + week_idx * 7 - first_weekday
        last_day_of_week = min(first_day_of_week + 6, days_in_month)
        week_warnings = [n for n in warning_notes if any(int(n.split('日')[0]) in range(max(1, first_day_of_week), last_day_of_week + 1) for n in [n] if n.split('日')[0].isdigit())]
        # Simplify: just show the week label
        lines.append(f'    <div style="text-align:left;font-size:12px;color:#8b949e;margin:-15px 0 20px 5px;">{week_label}</div>')
        lines.append('')

    # ========== 色块标注区 ==========
    lines.append('    <!-- ========== 色块标注区 ========== -->')
    lines.append('    <div class="legend-section">')
    lines.append('        <h3>🎨 色块标注说明</h3>')
    lines.append('        <div class="legend-grid">')

    legend_blocks = get_legend_blocks(year, month)
    for block in legend_blocks:
        lines.append('')
        lines.append(f'            <!-- {block["title"]} -->')
        lines.append('            <div class="legend-block">')
        lines.append(f'                <div class="legend-header"><span class="color-dot" style="background:{block["color"]};"></span>{block["title"]}</div>')
        lines.append('                <div class="legend-detail">')
        lines.append(f'                    {block["detail"]}')
        lines.append('                    <ul>')
        for item in block['items']:
            lines.append(f'                        <li>{item}</li>')
        lines.append('                    </ul>')
        lines.append('                </div>')
        lines.append('            </div>')

    lines.append('')
    lines.append('        </div>')
    lines.append('    </div>')
    lines.append('')
    lines.append('    <div class="footer-note">')
    lines.append('        <strong>📋 数据来源与说明</strong><br>')
    lines.append('        1. 🇨🇳 <strong>中国宏观数据</strong>：国家统计局/央行常规发布日历。CPI/PPI每月9日，PMI月末最后一日，工业/零售/固投每月15日前后，LPR每月20日。<br>')
    lines.append('        2. 🇺🇸 <strong>美股财报</strong>：来源各公司IR页面。财报日期可能调整，以公司官方公告为准。<br>')
    lines.append('        3. 🇺🇸 <strong>FOMC</strong>：美联储官方法定会议日程。<br>')
    lines.append('        4. 📊 <strong>期权/期货交割</strong>：来源各交易所及期货公司公告，日期以交易所实际执行为准。<br>')
    lines.append('        5. 🟡 <strong>部分数据</strong>：台股/日股/欧股财报暂无数据，待各公司公布具体日期后补充。<br>')
    lines.append('        6. ⚠️ 本日历仅收录固定排期事件，突发事件（如临时政策会议、数据推迟发布等）无法提前预告。<br>')
    lines.append('        7. ⚠️ 2027年A股休市日期为推算值，暂定待官方公布确认。<br>')
    lines.append('        8. 数据来源：国家统计局、中国人民银行、美联储、各交易所及公司IR页面。仅供参考，不构成投资建议。<br>')
    lines.append('    </div>')
    lines.append('')
    lines.append('    <div class="update-time">')
    lines.append(f'        重要日历 · {month_name}版 · 全18类事件覆盖<br>')
    lines.append('        数据来源：国家统计局/央行/美联储/各交易所/公司IR | 仅供参考，不构成投资建议<br>')
    lines.append(f'        更新日期：{today_date.strftime("%Y-%m-%d")}')
    lines.append('    </div>')
    lines.append('</div>')
    lines.append('</body>')
    lines.append('</html>')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='生成重要日历HTML文件')
    parser.add_argument('--start-year', type=int, default=2026, help='起始年份')
    parser.add_argument('--start-month', type=int, default=6, help='起始月份')
    parser.add_argument('--end-year', type=int, default=2027, help='结束年份')
    parser.add_argument('--end-month', type=int, default=12, help='结束月份')
    parser.add_argument('--output-dir', type=str, default='/app/data/所有对话/主对话/重要日历', help='输出目录')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    today = date.today()

    year = args.start_year
    month = args.start_month
    generated = []
    while True:
        if year > args.end_year or (year == args.end_year and month > args.end_month):
            break

        filename = f'重要日历_{year}{month:02d}.html'
        filepath = os.path.join(args.output_dir, filename)
        html = generate_month_html(year, month, today)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'✅ 已生成: {filename}')
        generated.append(filename)

        month += 1
        if month > 12:
            month = 1
            year += 1

    print(f'\n🎉 完成！共生成 {len(generated)} 个日历文件')
    print(f'📁 输出目录: {args.output_dir}')
    for g in generated:
        print(f'   - {g}')


if __name__ == '__main__':
    main()
