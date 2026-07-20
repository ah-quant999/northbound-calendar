"""
通用密码保护注入工具
给任意HTML页面注入密码验证遮罩，不影响页面原有功能
"""
import re

PASSWORD = "hjd666"
STORAGE_KEY = "portal_login_expire"
EXPIRE_DAYS = 7

CSS_TEMPLATE = """
/* ===== password-protect-v1 ===== */
.__pwd-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: #0d1117;
    z-index: 2147483647;
    display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.__pwd-box {
    background: #161b22;
    border-radius: 16px;
    border: 1px solid __THEME__66;
    padding: 36px 32px;
    text-align: center;
    max-width: 360px;
    width: 90%;
    box-shadow: 0 8px 32px __THEME__22;
}
.__pwd-icon {
    width: 48px; height: 48px;
    background: linear-gradient(135deg, __THEME__, __THEME__aa);
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; margin: 0 auto 12px;
}
.__pwd-title {
    font-size: 18px; color: #c9d1d9;
    font-weight: 600; margin-bottom: 4px;
}
.__pwd-sub {
    color: #6e7681; font-size: 12px;
    margin-bottom: 20px;
}
.__pwd-input {
    width: 100%; padding: 10px 14px;
    background: #0d1117;
    border: 1.5px solid #30363d;
    border-radius: 8px; color: #c9d1d9;
    font-size: 14px; outline: none;
    margin-bottom: 12px;
    box-sizing: border-box;
}
.__pwd-input:focus {
    border-color: __THEME__;
    box-shadow: 0 0 0 3px __THEME__22;
}
.__pwd-btn {
    width: 100%; padding: 10px;
    background: linear-gradient(135deg, __THEME__, __THEME__cc);
    border: none; border-radius: 8px;
    color: #fff; font-size: 14px; font-weight: 600;
    cursor: pointer; transition: all 0.2s;
}
.__pwd-btn:hover { opacity: 0.9; }
.__pwd-err {
    color: #f85149; font-size: 12px;
    margin-top: 8px; display: none;
}
.__pwd-hint {
    color: #6e7681; font-size: 11px;
    margin-top: 12px;
}
"""

OVERLAY_HTML = """
<!-- password-protect-v1 -->
<div class="__pwd-overlay" id="__pwdOverlay">
    <div class="__pwd-box">
        <div class="__pwd-icon">&#128274;</div>
        <div class="__pwd-title">访问验证</div>
        <div class="__pwd-sub">请输入密码</div>
        <input type="password" class="__pwd-input" id="__pwdInput" placeholder="请输入访问密码" autocomplete="off"
               onkeydown="if(event.key==='Enter')__pwdVerify()">
        <button class="__pwd-btn" onclick="__pwdVerify()">进入</button>
        <div class="__pwd-err" id="__pwdError">密码错误，请重试</div>
        <div class="__pwd-hint">7天内免输入 · 全站通用</div>
    </div>
</div>
"""

JS_CODE = """
<script>
(function(){
    var PWD = "__PWD__";
    var KEY = "__KEY__";
    var DAYS = __DAYS__;

    function ok() {
        try {
            var t = parseInt(localStorage.getItem(KEY) || "0", 10);
            if (t > Date.now()) return true;
            if (sessionStorage.getItem("portal_role") === "1") return true;
        } catch(e) {}
        return false;
    }

    function setOk() {
        try {
            var t = Date.now() + DAYS * 86400000;
            localStorage.setItem(KEY, String(t));
            sessionStorage.setItem("portal_role", "1");
        } catch(e) {}
    }

    window.__pwdVerify = function() {
        var inp = document.getElementById("__pwdInput");
        var err = document.getElementById("__pwdError");
        var v = inp ? inp.value : "";
        if (v === PWD) {
            setOk();
            var o = document.getElementById("__pwdOverlay");
            if (o) { o.style.display = "none"; }
        } else {
            if (err) err.style.display = "block";
            if (inp) { inp.focus(); inp.select(); }
        }
    };

    function init() {
        if (ok()) {
            var o = document.getElementById("__pwdOverlay");
            if (o) o.style.display = "none";
            return;
        }
        setTimeout(function(){
            var i = document.getElementById("__pwdInput");
            if (i) i.focus();
        }, 50);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
</script>
"""


def inject_password(html_path, theme_color="#ff7a00"):
    """给HTML文件注入密码验证遮罩
    返回True表示注入成功，False表示已有密码或无需注入
    """
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # 已有密码验证则跳过
    if "password-protect-v1" in html:
        return False
    
    # 构建CSS
    css = CSS_TEMPLATE.replace("__THEME__", theme_color)
    
    # 注入CSS到第一个<style>标签内
    if "<style>" in html:
        html = html.replace("<style>", "<style>\n" + css, 1)
    else:
        html = html.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
    
    # 注入HTML遮罩到<body>后
    body_match = re.search(r'<body[^>]*>', html)
    if body_match:
        html = html[:body_match.end()] + OVERLAY_HTML + html[body_match.end():]
    
    # 注入JS到</body>前
    js = JS_CODE.replace("__PWD__", PASSWORD).replace("__KEY__", STORAGE_KEY).replace("__DAYS__", str(EXPIRE_DAYS))
    if "</body>" in html:
        html = html.replace("</body>", js + "</body>", 1)
    else:
        html += js
    
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        path = sys.argv[1]
        color = sys.argv[2] if len(sys.argv) > 2 else "#ff7a00"
        ok = inject_password(path, color)
        print(f"{'Injected' if ok else 'Skipped'}: {path}")
    else:
        print("Usage: python password_protect.py <html_path> [theme_color]")
