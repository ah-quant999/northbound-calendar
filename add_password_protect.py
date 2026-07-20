#!/usr/bin/env python3
"""
给所有HTML页面添加密码验证逻辑（localStorage + 7天有效期）
密码: hjd666
键名: portal_login_expire（存过期时间戳）
"""

import os
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent

# 各页面主题色配置（根据页面主题色微调登录遮罩按钮颜色）
# 格式: {文件名关键词: (主色, 渐变起始色, 渐变结束色, 阴影色)}
THEME_COLORS = {
    # 默认深色主题（GitHub风格）- 粉色系
    "default": ("#ffb5c7", "#ffb5c7", "#ffd1b5", "rgba(255,181,199,0.35)"),
    # 北向资金 - 薄荷绿
    "northbound": ("#58a6ff", "#58a6ff", "#1f6feb", "rgba(88,166,255,0.35)"),
    "北向资金": ("#58a6ff", "#58a6ff", "#1f6feb", "rgba(88,166,255,0.35)"),
    # 每日洞察 - 橙色
    "insight": ("#ff7a00", "#ff9540", "#ff7a00", "rgba(255,122,0,0.35)"),
    "daily-insight": ("#ff7a00", "#ff9540", "#ff7a00", "rgba(255,122,0,0.35)"),
    # 信号说明 - 紫色系
    "signal-guide": ("#a78bfa", "#c4b5fd", "#a78bfa", "rgba(167,139,250,0.35)"),
    "信号说明": ("#a78bfa", "#c4b5fd", "#a78bfa", "rgba(167,139,250,0.35)"),
    # 机游共振 - 蜜桃粉
    "jiyou": ("#e8a0b0", "#e8a0b0", "#f0b7c0", "rgba(232,160,176,0.35)"),
    "机游共振": ("#e8a0b0", "#e8a0b0", "#f0b7c0", "rgba(232,160,176,0.35)"),
    "jiyou-signal": ("#e8a0b0", "#e8a0b0", "#f0b7c0", "rgba(232,160,176,0.35)"),
    # 重要日历 - 薰衣草紫
    "重要日历": ("#a78bfa", "#c4b5fd", "#a78bfa", "rgba(167,139,250,0.35)"),
    "important": ("#a78bfa", "#c4b5fd", "#a78bfa", "rgba(167,139,250,0.35)"),
    # 首页/索引 - 蓝色系
    "index": ("#58a6ff", "#58a6ff", "#1f6feb", "rgba(88,166,255,0.35)"),
}


def get_theme_color(filename: str) -> tuple:
    """根据文件名选择主题色"""
    name_lower = filename.lower()
    name = filename
    for keyword, colors in THEME_COLORS.items():
        if keyword in name_lower or keyword in name:
            return colors
    return THEME_COLORS["default"]


def generate_login_overlay(theme_colors: tuple) -> str:
    """生成登录遮罩的HTML + CSS + JS代码"""
    primary_color, grad_start, grad_end, shadow_color = theme_colors

    return f'''
<!-- ===== 密码验证遮罩 (开始) ===== -->
<style>
    .__login-overlay {{
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(13, 17, 23, 0.85);
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 999999;
        padding: 20px;
    }}
    .__login-box {{
        background: #161b22;
        border-radius: 20px;
        border: 1px solid #30363d;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        padding: 48px 40px;
        text-align: center;
        max-width: 420px;
        width: 100%;
    }}
    .__login-box .__logo-icon {{
        width: 56px;
        height: 56px;
        background: linear-gradient(135deg, {grad_start}, {grad_end});
        border-radius: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 28px;
        margin: 0 auto 14px;
    }}
    .__login-box h1 {{
        font-size: 22px;
        color: #c9d1d9;
        font-weight: 700;
        letter-spacing: 1px;
        margin-bottom: 4px;
    }}
    .__login-box .__subtitle {{
        color: #8b949e;
        font-size: 13px;
        margin-bottom: 28px;
    }}
    .__login-box input {{
        width: 100%;
        padding: 12px 16px;
        background: #0d1117;
        border: 1.5px solid #30363d;
        border-radius: 10px;
        color: #c9d1d9;
        font-size: 15px;
        outline: none;
        transition: all 0.2s;
        margin-bottom: 14px;
        font-family: inherit;
        box-sizing: border-box;
    }}
    .__login-box input:focus {{
        border-color: {primary_color};
        box-shadow: 0 0 0 3px {shadow_color};
    }}
    .__login-box input::placeholder {{ color: #6e7681; }}
    .__login-box .__btn {{
        width: 100%;
        padding: 12px;
        background: linear-gradient(135deg, {grad_start}, {grad_end});
        border: none;
        border-radius: 10px;
        color: #fff;
        font-size: 15px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s;
        font-family: inherit;
    }}
    .__login-box .__btn:hover {{
        transform: translateY(-1px);
        box-shadow: 0 4px 16px {shadow_color};
    }}
    .__login-box .__error {{
        color: #f85149;
        font-size: 13px;
        margin-top: 10px;
        display: none;
    }}
    .__login-box .__hint {{
        color: #6e7681;
        font-size: 12px;
        margin-top: 18px;
    }}
</style>
<div class="__login-overlay" id="__loginOverlay">
    <div class="__login-box">
        <div class="__logo-icon">🔒</div>
        <h1>访问验证</h1>
        <div class="__subtitle">请输入密码以查看本页内容</div>
        <input type="password" id="__pwdInput" placeholder="输入访问密码" autocomplete="off"
               onkeydown="if(event.key==='Enter')__verifyPassword()">
        <button class="__btn" onclick="__verifyPassword()">进入页面</button>
        <div class="__error" id="__pwdError">密码错误，请重试</div>
        <div class="__hint">密码登录 · 7天内免输入</div>
    </div>
</div>
<script>
(function() {{
    const __CAL_PWD = "hjd666";
    const __EXPIRE_KEY = "portal_login_expire";
    const __EXPIRE_DAYS = 7;

    function __isLoggedIn() {{
        try {{
            const expire = parseInt(localStorage.getItem(__EXPIRE_KEY) || "0", 10);
            return expire > Date.now();
        }} catch(e) {{
            return false;
        }}
    }}

    function __setLoggedIn() {{
        try {{
            const expire = Date.now() + __EXPIRE_DAYS * 24 * 60 * 60 * 1000;
            localStorage.setItem(__EXPIRE_KEY, String(expire));
            // 兼容旧版 portal.html 的 sessionStorage
            sessionStorage.setItem("portal_role", "1");
        }} catch(e) {{}}
    }}

    window.__verifyPassword = function() {{
        const input = document.getElementById("__pwdInput");
        const error = document.getElementById("__pwdError");
        const pwd = input ? input.value : "";
        if (pwd === __CAL_PWD) {{
            __setLoggedIn();
            const overlay = document.getElementById("__loginOverlay");
            if (overlay) {{
                overlay.style.opacity = "0";
                overlay.style.transition = "opacity 0.25s";
                setTimeout(function() {{ overlay.style.display = "none"; }}, 250);
            }}
        }} else {{
            if (error) error.style.display = "block";
            if (input) {{
                input.focus();
                input.select();
            }}
        }}
    }};

    function __initAuth() {{
        if (__isLoggedIn()) {{
            const overlay = document.getElementById("__loginOverlay");
            if (overlay) overlay.style.display = "none";
            return;
        }}
        // 未登录，确保遮罩显示（body隐藏内容）
        const overlay = document.getElementById("__loginOverlay");
        if (overlay) {{
            overlay.style.display = "flex";
            setTimeout(function() {{
                const input = document.getElementById("__pwdInput");
                if (input) input.focus();
            }}, 100);
        }}
    }}

    if (document.readyState === "loading") {{
        document.addEventListener("DOMContentLoaded", __initAuth);
    }} else {{
        __initAuth();
    }}
}})();
</script>
<!-- ===== 密码验证遮罩 (结束) ===== -->
'''


def add_password_to_file(filepath: Path) -> bool:
    """给单个HTML文件添加密码验证"""
    if not filepath.is_file():
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"  ❌ 读取失败: {filepath.name} - {e}")
        return False

    # 检查是否已经添加过
    if "__login-overlay" in content or "__CAL_PWD" in content:
        print(f"  ⏭  已存在密码验证: {filepath.name}")
        return True

    # 选主题色
    colors = get_theme_color(filepath.name)

    # 生成登录代码
    login_code = generate_login_overlay(colors)

    # 插入到 <body> 标签之后（body开标签后第一个位置）
    # 匹配 <body ...> 形式（可能有属性）
    body_pattern = r'(<body[^>]*>)'
    m = re.search(body_pattern, content, re.IGNORECASE)
    if not m:
        print(f"  ❌ 未找到<body>标签: {filepath.name}")
        return False

    content = re.sub(
        body_pattern,
        r'\1' + login_code,
        content,
        count=1,
        flags=re.IGNORECASE
    )

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  ✅ 已添加密码: {filepath.name}")
        return True
    except Exception as e:
        print(f"  ❌ 写入失败: {filepath.name} - {e}")
        return False


def main():
    print("=" * 60)
    print("🔐 为HTML页面添加密码验证")
    print("=" * 60)

    # 排除的文件
    exclude_files = {
        "portal.html",           # 已有完整登录逻辑
        "daily-insight-preview.html",  # 预览页，不需要密码
    }

    # 1. 根目录下所有HTML
    html_files = sorted(REPO_DIR.glob("*.html"))

    # 2. 过滤掉排除文件和非目标文件
    target_files = []
    for f in html_files:
        if f.name in exclude_files:
            continue
        # 排除明显的模板/片段文件（如果有）
        if "片段" in f.name:
            continue
        target_files.append(f)

    print(f"\n找到 {len(target_files)} 个目标HTML文件:")
    for f in target_files:
        print(f"  - {f.name}")

    print(f"\n开始处理...")
    success = 0
    fail = 0

    for f in target_files:
        if add_password_to_file(f):
            success += 1
        else:
            fail += 1

    print(f"\n{'=' * 60}")
    print(f"✅ 处理完成: 成功 {success} 个，失败 {fail} 个")
    print(f"{'=' * 60}")

    return fail == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
