import re
import os

files = [
    '/tmp/nb-calendar/重要日历_202607.html',
    '/tmp/nb-calendar/机游共振日历.html',
    '/tmp/nb-calendar/index.html',
]
# 还有所有重要日历的其他月份
import glob
more_files = glob.glob('/tmp/nb-calendar/重要日历_2026*.html') + glob.glob('/tmp/nb-calendar/重要日历_2027*.html')
files = list(set(files + more_files))

for fp in files:
    if not os.path.exists(fp):
        continue
    with open(fp, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. 删除底部原有的 back-to-portal
    content = re.sub(
        r'\s*<div class="back-to-portal">\s*<a href="portal\.html">← 返回九宝日历精选</a>\s*</div>',
        '',
        content
    )
    
    # 2. 删除旧的 back-link（← 返回登录页）
    content = re.sub(
        r'\s*<div class="back-link">\s*<a href="portal\.html">← 返回登录页</a>\s*</div>',
        '',
        content
    )
    
    # 3. 在 <div class="container"> 后面添加左上角醒目的返回按钮
    back_btn = '''
    <!-- 返回九宝日历精选 -->
    <a href="portal.html" class="back-to-portal-btn">← 九宝日历精选</a>
'''
    content = content.replace('<div class="container">', '<div class="container">' + back_btn)
    
    # 4. 在 </head> 前添加按钮样式
    btn_style = '''
    <style>
        .back-to-portal-btn {
            position: absolute;
            top: 18px;
            left: 18px;
            display: inline-block;
            padding: 10px 20px;
            background: linear-gradient(135deg, #ff6b6b, #ee5a24);
            color: #fff !important;
            font-size: 15px;
            font-weight: 700;
            text-decoration: none;
            border-radius: 25px;
            box-shadow: 0 4px 15px rgba(238, 90, 36, 0.5);
            z-index: 1000;
            transition: all 0.3s ease;
            letter-spacing: 0.5px;
        }
        .back-to-portal-btn:hover {
            transform: scale(1.08);
            box-shadow: 0 6px 20px rgba(238, 90, 36, 0.7);
            background: linear-gradient(135deg, #ff7b7b, #ff6a3d);
        }
        .container {
            position: relative;
        }
    </style>
'''
    content = content.replace('</head>', btn_style + '\n</head>')
    
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ 已更新: {os.path.basename(fp)}")

print("\n全部完成！")
