import json
import base64


"""
zako 签到助手 —— 适配CHD
工作流：
 1. 主页点猫爪 -> 启动浏览器 / CAS 登录
 2. 拿到 cookie + student_id -> 拉取课程列表 -> 跳课程页
 3. 点课程 -> 查最新签到码 -> 跳结果页
 4. 结果页可返回课程页继续查；任何页面右上角日志按钮可展开日志
"""

import asyncio
import math
import os
import re
import sys
import threading
import uuid
import requests
import tkinter as tk
import customtkinter as ctk
from playwright.async_api import async_playwright
from datetime import datetime, timezone, timedelta, time

# ── 颜色 / 字体常量 ────────────────────────────────────────
BG        = "#0F0E17"
SURFACE   = "#1A1828"
SURFACE2  = "#221F33"
ACCENT    = "#FF6B9D"
ACCENT_DK = "#CC4477"
TEXT_PRI  = "#FFFFFE"
TEXT_SEC  = "#A7A9BE"
SUCCESS   = "#06D6A0"
WARN      = "#FFD166"
DANGER    = "#EF476F"

BASE_URL = "https://course-online.chd.edu.cn"   # 注意是 HTTPS
HEADERS_BASE = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
    ),
}

BASE_URL = "https://course-online.chd.edu.cn"  # 注意使用 HTTPS
HEADERS_BASE = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0",
}

import json
import base64
import re

def is_headless_env():
    """判断当前是否为无 GUI 环境"""
    # 显式强制 CLI 模式
    if os.environ.get("CHAOXING_CLI") == "1":
        return True

    # Linux 且无 DISPLAY / WAYLAND_DISPLAY → 无头
    if sys.platform.startswith("linux"):
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return True

    # Windows / macOS 默认有 GUI
    return False


def load_credentials():
    """从环境变量或 config.json 读取账号密码"""
    username = os.environ.get("CHAOXING_USERNAME", "").strip()
    password = os.environ.get("CHAOXING_PASSWORD", "").strip()

    if not username or not password:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                username = cfg.get("USERNAME", "").strip()
                password = cfg.get("PASSWORD", "").strip()
            except Exception:
                pass

    return username, password

async def login_and_get_cookie(log=print, username=None, password=None, headless=None):
    """
    自动登录。
    - username/password: 若为 None，则从 env/config.json/input 获取
    - headless: 若为 None，自动判断（CLI 模式为 True，GUI 模式为 False）
    """
    # ---- 1. 获取账号密码 ----
    if not username or not password:
        username, password = load_credentials()
    if not username or not password:
        if is_headless_env():
            log("❌ 呜呜呜，咱没有找到账号密码呢喵...请主人在 config.json 中配置 CHAOXING_USERNAME / CHAOXING_PASSWORD喵！")
            return None, None
        log("❤ 初次见面，请主人输入长安大学统一认证账号和密码哦 ❤")
        username = input("学号: ").strip()
        password = input("密码: ").strip()
        save = input("是否希望咱记住主人的身份信息呢?请选择哦~(y/n): ").strip().lower()
        if save == "y":
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"USERNAME": username, "PASSWORD": password}, f, ensure_ascii=False, indent=2)
            log(f"✅ 账号被咱藏在了 {config_path}呢~咱保证一定会保守秘密的喵！")

    # ---- 2. 决定 headless ----
    if headless is None:
        headless = is_headless_env()
    log(f"根据主人的设备，咱决定选择: {'无头' if headless else '有头'}模式启动自己哦~(注：在有头模式的时候请主人不要乱动咱启动的浏览器哦~否则咱会找不到界面的呜呜，乱动的话咱咬你哦ww)")

    async with async_playwright() as p:
        browser = None
        for channel in ["msedge", "chrome"]:
            try:
                browser = await p.chromium.launch(headless=headless, channel=channel)
                break
            except Exception:
                continue
        if browser is None:
            browser = await p.chromium.launch(headless=headless)

        context = await browser.new_context()
        page = await context.new_page()

        try:
            # ---- 3. 打开首页 ----
            log("🌐 已经打开主人的Tronclass首页啦！")
            await page.goto(BASE_URL, wait_until="networkidle", timeout=25000)
            await asyncio.sleep(1)

            # ---- 4. 检查是否已登录 ----
            cookies = await context.cookies()
            has_role_token = any(c["name"] == "role_token" for c in cookies)

            if not has_role_token:
                log("🔓 主人似乎没有登录呢~让咱帮主人登录一下吧！")
                clicked = await _click_login_button(page, log)

                if not clicked:
                    log("⚠️ 呜呜，坏学校把登录按钮藏在了zako找不到的地方呢！咱直接访问 /user/courses 了哦，失礼失礼~")
                    try:
                        await page.goto(f"{BASE_URL}/user/courses", wait_until="commit", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                # ---- 5. CAS 表单填写 ----
                if "ids.chd.edu.cn" in page.url or "authserver" in page.url:
                    log("🔐 找到统一认证页了呢，奋笔疾书中...")
                    await _fill_cas_form(page, username, password, log)
                elif "role_token" in await page.evaluate("() => document.cookie"):
                    log("ℹ️ 欸?主人原来已经帮zako登录过了吗?谢谢主人喵~~")
                else:
                    log(f"ℹ️ zako正在  {page.url} 里面找东西呢~")
                    if headless:
                        log("❌ 呜喵，坏学校似乎使用了某种未知力量，当前无头模式下zako无法帮主人输入喵...")
                        await browser.close()
                        return None, None
                    log("⚠️ ww麻烦主人帮zako手动登录一下嘛，谢谢~")
                    await browser.close()
                    browser = await p.chromium.launch(headless=False, channel="msedge")
                    context = await browser.new_context()
                    page = await context.new_page()
                    await page.goto(BASE_URL, wait_until="commit")
                    await page.wait_for_function(
                        "() => document.cookie.includes('role_token')",
                        timeout=180000
                    )

            # ---- 6. 等页面稳定 + 提取 Cookie ----
            log("⏳ 等待页面跳转中...zako先去玩一会毛线球哦>w<")
            try:
                await page.wait_for_url(f"{BASE_URL}/**", timeout=20000, wait_until="commit")
            except Exception:
                pass
            await asyncio.sleep(2)

            cookies = await context.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            log(f"✅ 找到 {len(cookies)} 个 Cookie了呢，看上去好好吃~")

            # ---- 7. 解析 uid ----
            student_id = None
            for item in cookie_str.split(";"):
                item = item.strip()
                if item.startswith("role_token="):
                    token = item.split("=", 1)[1]
                    parts = token.split(".")
                    if len(parts) >= 2:
                        for part in [parts[0], parts[1]]:
                            try:
                                decoded = part + "=" * (4 - len(part) % 4)
                                decoded = decoded.replace("-", "+").replace("_", "/")
                                data = json.loads(base64.b64decode(decoded))
                                uid = data.get("uid")
                                if uid:
                                    student_id = str(uid)
                                    log(f"✅ 吃饼干的时候，发现主人的学生ID: {student_id}了呢~")
                                    break
                            except Exception:
                                continue
                    break

            await browser.close()

            if not student_id and not headless:
                student_id = input("学生ID: ").strip()

            return cookie_str, student_id

        except Exception as e:
            log(f"❌ 呜呜呜...登录出现未知错误了呢TwT: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            return None, None


async def _click_login_button(page, log):
    """尝试在页面上找到并点击登录按钮"""
    # 长安大学畅课首页右上角的登录按钮，可能是 a / button / div
    selectors = [
        "a:has-text('登录')",
        "button:has-text('登录')",
        "span:has-text('登录')",
        "div:has-text('登录')",
        "a[href*='login']",
        "a[href*='authserver']",
        "a[href*='cas']",
        "[class*='login']",
        "[class*='Login']",
        "[id*='login']",
        "[id*='Login']",
    ]
    for sel in selectors:
        try:
            elem = await page.query_selector(sel)
            if elem:
                # 确保可见
                visible = await elem.is_visible()
                if not visible:
                    continue
                await elem.click()
                log(f"✅ 找到登录按钮了w (选择器: {sel})")
                # 等待页面响应
                await asyncio.sleep(2)
                return True
        except Exception:
            continue

    # 兜底：尝试通过文本查找点击
    try:
        await page.click("text=登录", timeout=3000)
        log("✅ 文本选择器帮咱点击登录了！谢谢~")
        await asyncio.sleep(2)
        return True
    except Exception:
        pass

    return False


async def _fill_cas_form(page, username, password, log):
    """填写长安大学 CAS 登录表单（金智教育版）"""
    log("📝 正在等待 CAS 表单加载...咱有点困了呢~主人早八会不会像咱一样困呀?可以像zako一样喝一杯咖啡哦>.<")

    try:
        await page.wait_for_selector("input[type='password']", timeout=20000, state="visible")
        log("✅ CAS 表单出现了！")
    except Exception as e:
        log(f"❌ CAS 表单藏在: {e}里面不肯出来，呜呜呜")
        raise Exception("CAS 表单未加载")

    await asyncio.sleep(0.5)

    # ---- 填写用户名 ----
    username_selectors = [
        "input#username",
        "input[name='username']",
        "input[placeholder*='学号']",
        "input[placeholder*='账号']",
        "input[placeholder*='用户名']",
        "input[type='text']",
    ]
    filled_user = False
    for sel in username_selectors:
        try:
            await page.wait_for_selector(sel, timeout=5000, state="visible")
            elem = await page.query_selector(sel)
            if elem and await elem.is_enabled():
                # 关键：先清空再输入，用 type 模拟真实输入（触发 input 事件）
                await elem.click()
                await elem.fill("")
                await elem.type(username, delay=30)
                filled_user = True
                log(f"✅ 终于写完用户名了~ (由选择器: {sel}帮忙写入，谢谢喵~)")
                break
        except Exception:
            continue

    # ---- 填写密码 ----
    password_selectors = [
        "input#password",
        "input[name='password']",
        "input[type='password']",
    ]
    filled_pwd = False
    for sel in password_selectors:
        try:
            await page.wait_for_selector(sel, timeout=5000, state="visible")
            elem = await page.query_selector(sel)
            if elem and await elem.is_enabled():
                await elem.click()
                await elem.fill("")
                await elem.type(password, delay=30)
                filled_pwd = True
                log("✅ 写完密码了！")
                break
        except Exception:
            continue

    if not filled_user or not filled_pwd:
        log("⚠️ 未能填写账号或密码呢，www")
        raise Exception("自动填写失败")

    # 让前端 JS 有时间响应输入事件（重要！）
    await asyncio.sleep(0.5)

    # ---- 点击登录按钮（关键修复：用 a#login_submit）----
    submit_selectors = [
        "a#login_submit",
        "#login_submit",
        "a[onclick*='startLogin']",
        "a.login-btn",
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('登录')",
    ]
    clicked = False
    for sel in submit_selectors:
        try:
            await page.wait_for_selector(sel, timeout=3000, state="visible")
            elem = await page.query_selector(sel)
            if elem and await elem.is_enabled():
                await elem.click()
                clicked = True
                log(f"✅ 找到登录按钮啦！(谢谢选择器: {sel}喵)")
                break
        except Exception:
            continue

    # 如果都没找到，打印所有候选元素
    if not clicked:
        log("⚠️ 坏学校似乎把登录按钮藏起来了呢qwq，zako正在寻找页面所有 <a>/<button> 元素...")
        try:
            elems = await page.query_selector_all("a, button, input[type='submit']")
            for i, b in enumerate(elems[:40]):
                try:
                    tag = await b.evaluate("el => el.tagName")
                    text = (await b.inner_text() or "").strip()[:30]
                    cls = await b.get_attribute("class") or ""
                    bid = await b.get_attribute("id") or ""
                    log(f"  [{i}] <{tag}> id='{bid}' class='{cls}' text='{text}'")
                except Exception:
                    pass
        except Exception as e:
            log(f"打印候选失败了www: {e}")

        # 兜底：按回车
        await page.keyboard.press("Enter")
        log("✅ 一个都没有找到qwq！试试回车...")

    # ---- 等待登录完成 ----
    log("⏳ 等待登录跳转ing...")
    try:
        await page.wait_for_function(
            "() => document.cookie.includes('role_token=')",
            timeout=60000
        )
        log("✅ 检测到 role_token，登录成功啦！")
    except Exception as e:
        log(f"⚠️ 等待登录超时了呜呜: {e}")
        # 登录失败时 dump 页面错误提示
        try:
            err = await page.query_selector("#formErrorTip")
            if err:
                text = (await err.inner_text()).strip()
                if text:
                    log(f"❌ CAS 提示了一些信息，咱听不懂呜呜，请主人处理喵qwq: {text}")
        except Exception:
            pass
        raise Exception("登录未完成qwq")


def get_current_semester_info(cookie, log=print):
    headers = {**HEADERS_BASE, "cookie": cookie}

    sem_id = None
    year_id = None

    # 获取学期列表
    try:
        resp = requests.get(f"{BASE_URL}/api/my-semesters", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # 可能返回的是数组，也可能包含 semesters 字段
            if isinstance(data, list):
                semesters = data
            elif isinstance(data, dict):
                semesters = data.get('semesters', data.get('data', []))
            else:
                semesters = []
            log(f"📚 主人的学期列表被咱发现啦awa: {[s.get('name') for s in semesters]}")
            # 找当前学期（is_active 为 true）
            for s in semesters:
                if s.get('is_active', False):
                    sem_id = str(s['id'])
                    year_id = str(s.get('academic_year_id', ''))
                    log(f"✅ 主人的学期id在这里哦~: {s.get('name')} (id={sem_id}, year={year_id})")
                    break
            # 如果没有 active 的，取第一个
            if not sem_id and semesters:
                sem_id = str(semesters[0]['id'])
                year_id = str(semesters[0].get('academic_year_id', ''))
                log(f"⚠️ 奇怪呢，似乎没有当前活动的学期，只好使用第一个了w: id={sem_id}, year={year_id}")
        else:
            log(f"⚠️ 偷看学期列表被老师赶出来了(T.T) [{resp.status_code}]: {resp.text[:200]}")
    except Exception as e:
        log(f"⚠️ 获取学期列表出现异常情况w，请主人处理喵...: {e}")

    # 如果 year_id 还是空的，尝试单独获取学年列表（可选）
    if not year_id:
        try:
            resp = requests.get(f"{BASE_URL}/api/my-academic-years", headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                years = data if isinstance(data, list) else data.get('data', [])
                for y in years:
                    if y.get('is_active', False):
                        year_id = str(y['id'])
                        break
                if not year_id and years:
                    year_id = str(years[0]['id'])
        except Exception as e:
            log(f"⚠️ 获取学年列表出现未知异常ww，请主人处理喵...: {e}")

    # 如果仍然没有，使用从学期中解析到的 year_id（如果有）
    if not year_id:
        # 如果 sem_id 已知，可以从学期数据中找，但上面已经尝试了
        # 这里再给一个默认值
        year_id = "19"  # 根据你的实际情况调整，或者从学期数据中提取
        log(f"⚠️ 怎么一直找不到学期id呢呜呜呜，使用默认值: {year_id}")

    log(f"✅ 最终使用: 学期={sem_id}, 学年={year_id}")
    return sem_id, year_id



def get_courses(cookie, s_id, y_id, log=print):
    log("❤ 正在获取课程列表喵~❤...")
    headers = {
        **HEADERS_BASE,
        "cookie": cookie,
        "content-type": "application/json;charset=UTF-8",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/user/courses",
    }
    payload = {
        "fields": "id,name,course_code,department(id,name),grade(id,name),klass(id,name),course_type,cover,small_cover,start_date,end_date,is_started,is_closed,academic_year_id,semester_id,credit,compulsory,second_name,display_name,created_user(id,name),org(is_enterprise_or_organization),org_id,public_scope,audit_status,audit_remark,can_withdraw_course,imported_from,allow_clone,is_instructor,is_team_teaching,is_default_course_cover,archived,instructors(id,name,email,avatar_small_url),course_attributes(teaching_class_name,is_during_publish_period,copy_status,tip,data,audience_type,graduate_method),user_stick_course_record(id)",
        "page": 1,
        "page_size": 30,   # 你可以调整成 10 或 30
        "conditions": {
            "keyword": "",
            "classify_type": "recently_started",
            "display_studio_list": False
        },
        "showScorePassedStatus": False
    }
    try:
        resp = requests.post(f"{BASE_URL}/api/my-courses", headers=headers, json=payload, timeout=15)
        if resp.status_code != 200:
            log(f"⚠️ 请求课程列表的时候被赶出来了w [{resp.status_code}]")
            return []
        data = resp.json()
    except Exception as e:
        log(f"⚠️ 解析课程列表响应时发现它太高冷了qwq: {e}")
        return []

    # 解析返回的课程列表（兼容不同字段名）
    courses = []
    if isinstance(data, list):
        courses = data
    elif "courses" in data:
        courses = data["courses"]
    elif "data" in data:
        courses = data["data"]
    else:
        log("⚠️ zako看不懂课程列表数据结构qwq")
        return []

    # 去重（按 id）
    seen = set()
    unique = []
    for c in courses:
        cid = c.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(c)
    log(f"✅ 成功找到 {len(unique)} 门课程！zako好厉害~(自恋ing)")
    return unique






def auto_scan_all_courses(cookie, student_id, courses, log=print):
    """
    遍历所有课程，检测进行中的签到，并自动执行。
    返回一个结果列表，供 UI 展示。
    """
    import time
    from datetime import datetime, timezone, timedelta
    results = []
    total = len(courses)
    log(f"🚀 开始自动扫描 {total} 门课程...zako需要一个个看哦，主人请耐心一点~")

    for idx, course in enumerate(courses, 1):
        course_id = course.get("id")
        course_name = course.get("display_name") or course.get("name") or f"课程{course_id}"
        log(f"\n[{idx}/{total}] 🔍 扫描：{course_name}")

        try:
            latest = get_latest_rollcall(course_id, cookie, student_id)
        except Exception as e:
            log(f"  ⚠️ 获取签到记录的时候被惩罚了呜呜呜：{e}")
            results.append({"course": course_name, "status": "error", "msg": str(e)})
            time.sleep(1)
            continue

        if latest is None:
            log(f"  ℹ️ 还没有签到哦~")
            results.append({"course": course_name, "status": "none"})
            time.sleep(0.8)
            continue

            # ---- 提取关键字段 ----
        rid = str(latest.get("rollcall_id") or latest.get("id") or "")
            # 关键：活动状态用 rollcall_status，学生状态用 status
        rollcall_status = str(latest.get("rollcall_status") or latest.get("status") or "").lower()
        is_expired = bool(latest.get("is_expired", False))
        is_number = bool(latest.get("is_number"))
        is_radar = bool(latest.get("is_radar"))

        log(f"  📋 rollcall_id={rid}, 活动状态={rollcall_status}, number={is_number}, radar={is_radar}")

        # 只处理进行中的签到（兼容 active / on_call_fine 等状态）
        if rollcall_status in ("finished", "expired", "closed") or is_expired:
            log(f"  ⏭ 签到进行过了呢")
            results.append({"course": course_name, "status": "skipped", "reason": "finished"})
            time.sleep(0.8)
            continue

        # ---- 优先级1：数字签到 ----
        if is_number:
            code, code_status, _ = get_number_code(rid, cookie)
            if code and code_status in ("active", "on_call_fine"):
                log(f"  🎯 发现数字签到！签到码={code}，准备提交...")
                ok, info = submit_number_code(cookie, rid, log)
                results.append({
                    "course": course_name,
                    "status": "digital",
                    "ok": ok,
                    "code": code,
                    "info": info,
                })
                time.sleep(1)
                continue
            elif code_status in ("finished", "expired", "closed") or code_status is None:
                log(f"  ⏭ 数字签到已结束喵~")
                results.append({"course": course_name, "status": "finished"})
            else:
                log(f"  ⚠️ 是数字签到但zako似乎没有获取到码qwq，主人对不起>.<（code={code}, status={code_status}）")
            time.sleep(1)
            continue

        # ---- 优先级2：雷达签到 ----
        if is_radar:
            log(f"  📡 发现雷达签到！开始定位...")
            ok, info = send_radar(cookie, rid, log)
            results.append({
                "course": course_name,
                "status": "radar",
                "ok": ok,
                "info": info,
            })
            time.sleep(1)
            continue

        # ---- 其他类型 ----
        log(f"  ❓ zako不认识这个签到类型！qwq主人对不起")
        results.append({"course": course_name, "status": "unsupported"})
        time.sleep(0.8)

    # 汇总
    success = sum(1 for r in results if r.get("ok"))
    log(f"\n🎉 扫描完成啦！共 {total} 门课程，成功签到 {success} 次")
    return results
# ==============================================================================
# 后端逻辑（完美继承原有机制，仅增加 log 参数用于重定向输出到 UI）
# ==============================================================================


def get_latest_rollcall(course_id, cookie, student_id):
    headers = {**HEADERS_BASE, "cookie": cookie}
    url  = (
        f"{BASE_URL}/api/course/{course_id}"
        f"/student/{student_id}/rollcalls?page=1&page_size=10"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()
    except Exception:
        return None

    # 提取 rollcalls 列表
    if isinstance(data, list):
        rollcalls = data
    elif "rollcalls" in data:
        rollcalls = data["rollcalls"]
    elif "data" in data:
        rollcalls = data["data"]
    else:
        rollcalls = []

    if not rollcalls:
        return None

    # 遍历 rollcalls，提取所有 children 中的签到
    all_children = []
    for item in rollcalls:
        children = item.get("children", [])
        if children:
            # 如果有 children，则添加 children 中的每个签到，并保留父级信息（如合并签到时间）
            for child in children:
                # 将父级的 rollcall_time 和 title 可能合并到 child 中，以便后续使用
                child['parent_rollcall_time'] = item.get('rollcall_time')
                child['parent_title'] = item.get('title')
                all_children.append(child)
        else:
            # 如果无 children（个别情况），直接添加 item
            all_children.append(item)

    if not all_children:
        return None

    # 按 rollcall_time 排序，取最新的
    all_children.sort(key=lambda x: x.get('rollcall_time', ''), reverse=True)
    return all_children[0]   # 返回最新的一条


def get_number_code(rollcall_id, cookie):
    headers = {**HEADERS_BASE, "cookie": cookie}
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None, None, None
        data = resp.json()
        number_code = data.get('number_code')  # 可能为 None 或空
        status = data.get('status')
        end_time = data.get('end_time')
        return number_code, status, end_time
    except Exception as e:
        print(f"获取签到码失败: {e}")
        return None, None, None


def submit_number_code(cookie, rollcall_id, log=print):
    log(f"🐾 开始数字签到提交喵~ rollcall_id={rollcall_id}")
    code, status, end_time = get_number_code(rollcall_id, cookie)
    if not code:
        log("❌ 未获取到数字签到码w，提交失败了呢qwq")
        return False, {"reason": "no_code"}
    if status == "finished":
        log(f"⏰ 签到已经结束（截止：{end_time or '未知'}），不提交")
        return False, {"reason": "finished", "end_time": end_time}
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer_number_rollcall"
    payload = {"deviceId": str(uuid.uuid4()), "numberCode": str(code)}
    try:
        resp = requests.put(
            url, json=payload, headers={**HEADERS_BASE, "cookie": cookie}, timeout=15
        )
        if resp.status_code == 200:
            log(f"✅ 数字签到成功喵❤ 签到码：{code}")
            return True, {"code": code}
        log(f"❌ HTTP提交失败呜  {resp.status_code}：{resp.text[:200]}")
        return False, {"reason": "http", "status": resp.status_code, "code": code}
    except Exception as e:
        log(f"❌ 提交发生异常>.<：{e}")
        return False, {"reason": "exception", "error": str(e), "code": code}


# ==============================================================================
# 雷达签到引擎（四校区两阶段定位，移植自 zako_radar.py）
# ==============================================================================

RADAR_URL = f"{BASE_URL}/api/radar/rollcalls"

# 校区定义：中心点、南北长度、东西长度、旋转角（顺时针为正，逆时针为负）
CAMPUSES = [
    {
        "name": "雁塔校区北",
        "lat": 34.232984,
        "lng": 108.958543,
        "size_ns": 550,    # 南北长约 500 米
        "size_ew": 1500,   # 东西长约 1000 米
        "rotation": -17.6,     # 如有倾斜填角度，例如 -10
    },
    {
        "name": "渭水校区",
        "lat": 34.370293,
        "lng": 108.902411,
        "size_ns": 1500,   # 南北长约 1000 米
        "size_ew": 2500,   # 东西长约 2000 米
        "rotation": 0,     # 待测量后填入（例如 -15）
    },
    {
        "name": "雁塔校区南",
        "lat": 34.225758,
        "lng": 108.960683,
        "size_ns": 250,   # 南北长约 1000 米
        "size_ew": 750,   # 东西长约 2000 米
        "rotation": 0,
    },
    {
        "name": "小寨校区",
        "lat": 34.225758,
        "lng": 108.960683,
        "size_ns": 250,   # 南北长约 1000 米
        "size_ew": 300,   # 东西长约 2000 米
        "rotation": 0,



    }
]

EARTH_R = 6371000.0
PROBE_ACCURACY = 35

_radar_context = threading.local()

import random
from itertools import combinations

def get_campus_corners(campus):
    """
    计算校区四角坐标。
    返回顺序：[东北, 西北, 西南, 东南]
    """
    lat0, lng0 = campus["lat"], campus["lng"]
    half_ns = campus["size_ns"] / 2.0
    half_ew = campus["size_ew"] / 2.0
    theta = math.radians(campus.get("rotation", 0))

    # 局部平面坐标：x 向东，y 向北
    local_corners = [
        ( half_ew,  half_ns),   # 东北
        (-half_ew,  half_ns),   # 西北
        (-half_ew, -half_ns),   # 西南
        ( half_ew, -half_ns),   # 东南
    ]

    corners = []
    for x, y in local_corners:
        # 顺时针旋转 theta 角
        xr = x * math.cos(theta) + y * math.sin(theta)
        yr = -x * math.sin(theta) + y * math.cos(theta)
        # 转经纬度（小范围近似足够精确）
        dlat = yr / 111000.0
        dlng = xr / (111000.0 * math.cos(math.radians(lat0)))
        corners.append((lat0 + dlat, lng0 + dlng))
    return corners


def approx_distance(lat1, lng1, lat2, lng2):
    """局部平面近似的两点距离（米）"""
    lat_mid = (lat1 + lat2) / 2.0
    dy = (lat2 - lat1) * 111000.0
    dx = (lng2 - lng1) * 111000.0 * math.cos(math.radians(lat_mid))
    return math.hypot(dx, dy)


def trilaterate(points, distances):
    """
    三边测量：三个点及对应距离，求目标位置。
    points: [(lat,lng), (lat,lng), (lat,lng)]
    distances: [d1, d2, d3]
    返回: (lat, lng) 或 None
    """
    if len(points) != 3 or len(distances) != 3:
        return None

    # 以第一个点为原点做局部投影
    lat0, lng0 = points[0]
    xy = [latlon_to_xy(lat, lng, lat0, lng0) for lat, lng in points]
    (x1, y1), (x2, y2), (x3, y3) = xy
    d1, d2, d3 = distances

    # 线性化方程（两两相减）
    A = [[2*(x2-x1), 2*(y2-y1)],
         [2*(x3-x1), 2*(y3-y1)]]
    b = [d1*d1 - d2*d2 + x2*x2 + y2*y2 - x1*x1 - y1*y1,
         d1*d1 - d3*d3 + x3*x3 + y3*y3 - x1*x1 - y1*y1]

    det = A[0][0]*A[1][1] - A[0][1]*A[1][0]
    if abs(det) < 1e-6:
        return None  # 三点共线，无法定位

    X = (b[0]*A[1][1] - b[1]*A[0][1]) / det
    Y = (A[0][0]*b[1] - A[1][0]*b[0]) / det

    return xy_to_latlon(X, Y, lat0, lng0)


def find_active_radar_record(cookie, rollcall_id, log=None):
    try:
        resp = requests.get(
            RADAR_URL, headers={**HEADERS_BASE, "cookie": cookie}, timeout=15
        )
        data = resp.json()
    except Exception as e:
        if log:
            log(f"⚠️ 获取活动雷达列表失败: {e}")
        return None
    if isinstance(data, dict):
        rollcalls = data.get("rollcalls", [])
    elif isinstance(data, list):
        rollcalls = data
    else:
        rollcalls = []
    target = str(rollcall_id)
    for rc in rollcalls:
        if not isinstance(rc, dict):
            continue
        rid = str(rc.get("rollcall_id") or rc.get("id") or "")
        if rid == target:
            return rc
    return None


def radar_put(cookie, rollcall_id, lat, lng, timeout=15, session=None, device_id=None):
    if session is None:
        session = getattr(_radar_context, "session", None)
    if device_id is None:
        device_id = getattr(_radar_context, "device_id", None) or str(uuid.uuid4())
    payload = {
        "accuracy": PROBE_ACCURACY,
        "altitude": 0,
        "altitudeAccuracy": None,
        "deviceId": device_id,
        "heading": None,
        "latitude": lat,
        "longitude": lng,
        "speed": None,
    }
    client = session if session is not None else requests
    try:
        resp = client.put(
            f"{BASE_URL}/api/rollcall/{rollcall_id}/answer",
            json=payload,
            headers={**HEADERS_BASE, "cookie": cookie},
            timeout=timeout,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data
    except Exception as e:
        return 0, {"error": str(e)}


def radar_distance(data):
    if not isinstance(data, dict):
        return None
    for key in ("distance", "dist", "distance_m", "distanceMeters"):
        val = data.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def latlon_to_xy(lat, lng, lat0, lng0):
    x = math.radians(lng - lng0) * EARTH_R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_R
    return x, y


def xy_to_latlon(x, y, lat0, lng0):
    lat = lat0 + math.degrees(y / EARTH_R)
    lng = lng0 + math.degrees(x / (EARTH_R * math.cos(math.radians(lat0))))
    return lat, lng


def circle_intersections(x1, y1, d1, x2, y2, d2):
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist == 0:
        return None
    if dist > d1 + d2:
        if dist - (d1 + d2) <= 50.0:
            r = d1 / (d1 + d2)
            p = (x1 + (x2 - x1) * r, y1 + (y2 - y1) * r)
            return p, p
        return None
    if dist < abs(d1 - d2):
        if abs(d1 - d2) - dist <= 50.0:
            if d1 > d2:
                r = d1 / dist
                p = (x1 + (x2 - x1) * r, y1 + (y2 - y1) * r)
            else:
                r = d2 / dist
                p = (x2 + (x1 - x2) * r, y2 + (y1 - y2) * r)
            return p, p
        return None

    along = (d1 * d1 - d2 * d2 + dist * dist) / (2 * dist)
    h_sq = d1 * d1 - along * along
    if h_sq < 0:
        h_sq = 0.0
    height = math.sqrt(h_sq)
    mx = x1 + along * (x2 - x1) / dist
    my = y1 + along * (y2 - y1) / dist
    ox = -(y2 - y1) * height / dist
    oy = (x2 - x1) * height / dist
    return (mx + ox, my + oy), (mx - ox, my - oy)


def solve_two_points(lat1, lng1, lat2, lng2, d1, d2):
    lat0 = (lat1 + lat2) / 2
    lng0 = (lng1 + lng2) / 2
    x1, y1 = latlon_to_xy(lat1, lng1, lat0, lng0)
    x2, y2 = latlon_to_xy(lat2, lng2, lat0, lng0)
    sols = circle_intersections(x1, y1, d1, x2, y2, d2)
    if not sols:
        return None
    return (
        xy_to_latlon(sols[0][0], sols[0][1], lat0, lng0),
        xy_to_latlon(sols[1][0], sols[1][1], lat0, lng0),
    )


def radar_lock_campus(cookie, rollcall_id, log):
    """
    阶段一：粗定位。用各校区中心点探针，选距离最小的校区。
    如果某个中心点直接 200，说明在有效范围内，直接返回成功。
    """
    best_campus = None
    best_dist = None

    for c in CAMPUSES:
        status, data = radar_put(cookie, rollcall_id, c["lat"], c["lng"])
        if status == 200:
            log(f"🎯 {c['name']} 中心点直接命中！")
            return c, 0.0

        d = radar_distance(data)
        log(f"📡 {c['name']} 中心探针 distance={d}")
        if d is not None and (best_dist is None or d < best_dist):
            best_dist = d
            best_campus = c

    return best_campus, best_dist


def radar_triangulate(cookie, rollcall_id, campus, log):
    """
    阶段二：在选定校区内用四角探针做三边测量。
    - 四角各发一次探针
    - 随机取三个点做三边测量
    - 用第四个点验证（残差 = |理论距离 - 实际距离|）
    - 遍历所有三点组合，取残差最小的解
    """
    corners = get_campus_corners(campus)
    corner_names = ["东北", "西北", "西南", "东南"]

    # 向四个角发探针
    readings = []
    for i, (lat, lng) in enumerate(corners):
        status, data = radar_put(cookie, rollcall_id, lat, lng)
        if status == 200:
            log(f"🎯 {campus['name']} {corner_names[i]}角直接命中！")
            return True, (lat, lng)
        dist = radar_distance(data)
        log(f"📡 {campus['name']} {corner_names[i]}角 distance={dist}")
        if dist is not None:
            readings.append({"idx": i, "lat": lat, "lng": lng, "dist": dist})

    if len(readings) < 3:
        log(f"❌ 有效探针数不太够...呜呜呜：{len(readings)}/4")
        return False, None

    # 遍历所有三点组合
    indices = list(range(len(readings)))
    best_solution = None
    best_residual = float('inf')

    for combo in combinations(indices, 3):
        pts = [(readings[i]["lat"], readings[i]["lng"]) for i in combo]
        dists = [readings[i]["dist"] for i in combo]
        remaining = [i for i in indices if i not in combo]

        est = trilaterate(pts, dists)
        if est is None:
            continue

        # 用剩余点验证
        residual = 0.0
        for i in remaining:
            r = readings[i]
            exp_dist = approx_distance(est[0], est[1], r["lat"], r["lng"])
            residual += abs(exp_dist - r["dist"])

        log(f"🔍 组合 {combo}: 估计 ({est[0]:.6f},{est[1]:.6f}), 残差={residual:.1f}m")

        if residual < best_residual:
            best_residual = residual
            best_solution = est

    if best_solution is None:
        log("❌ 所有三点组合都失败了呜呜呜")
        return False, None

    # 向估计位置提交签到
    s, _ = radar_put(cookie, rollcall_id, best_solution[0], best_solution[1])
    if s == 200:
        log(f"✅ 三边定位成功啦！位置({best_solution[0]:.6f},{best_solution[1]:.6f})，残差{best_residual:.1f}m")
        return True, best_solution
    else:
        log(f"⚠️ 估计位置提交失败（HTTP {s}），残差{best_residual:.1f}m")
        return False, best_solution


def send_radar(cookie, rollcall_id, log=print):
    log(f"开始雷达签到 rollcall_id={rollcall_id}")
    session = requests.Session()
    device_id = str(uuid.uuid4())
    _radar_context.session = session
    _radar_context.device_id = device_id
    try:
        center, hit = radar_lock_campus(cookie, rollcall_id, log)
        if center is None:
            log("❌ 四校区探针都不理我(均未回传距离)呜呜,雷达签到失败")
            return False, {"campus": None, "position": None}
        if hit == 0.0:
            log(f"✅ 雷达签到成功（校区中心直接命中：{center['name']}）")
            return True, {"campus": center["name"], "position": (center["lat"], center["lng"])}
        log(f"📍 锁定校区：{center['name']}")
        ok, pos = radar_triangulate(cookie, rollcall_id, center, log)
        if ok:
            log(f"✅ 雷达签到成功，教师位置≈({pos[0]:.6f}, {pos[1]:.6f})")
        else:
            log("❌ 校区内精确定位失败qwq，对不起呜呜")
        return ok, {"campus": center["name"], "position": pos}
    finally:
        session.close()
        _radar_context.session = None
        _radar_context.device_id = None


# ==============================================================================
# 工具：在后台线程里跑 asyncio 事件循环
# ==============================================================================

def run_async(coro, callback):
    """在独立线程里运行 async 协程，完成后把结果用 callback 送回主线程。"""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
            callback(result, None)
        except Exception as e:
            callback(None, e)
        finally:
            loop.close()
    threading.Thread(target=_run, daemon=True).start()


def run_sync_in_thread(fn, callback, *args, **kwargs):
    """在独立线程里运行普通同步函数，完成后 callback 送回结果。"""
    def _run():
        try:
            result = fn(*args, **kwargs)
            callback(result, None)
        except Exception as e:
            callback(None, e)
    threading.Thread(target=_run, daemon=True).start()


# ==============================================================================
# UI 辅助组件
# ==============================================================================

def make_label(parent, text, size=13, color=TEXT_PRI, bold=False, anchor="w", wraplength=0):
    weight = "bold" if bold else "normal"
    return ctk.CTkLabel(
        parent, text=text, font=("Microsoft YaHei", size, weight),
        text_color=color, anchor=anchor, wraplength=wraplength
    )


def make_button(parent, text, command, fg=ACCENT, hover=ACCENT_DK, width=200, height=40, size=13):
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=fg, hover_color=hover, text_color=BG,
        font=("Microsoft YaHei", size, "bold"),
        width=width, height=height, corner_radius=12,
    )


def separator(parent):
    return ctk.CTkFrame(parent, height=1, fg_color=SURFACE2)


def fmt_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


# ==============================================================================
# 主应用
# ==============================================================================

class ZakoApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # ── 窗口基础设置 ─────────────────────────────
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title("Zako 签到助手 ❤")
        self.geometry("500x700")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        png_candidates = [
            os.path.join(getattr(sys, "_MEIPASS", ""), "assets", "nekonn.png"),
            os.path.join(getattr(sys, "_MEIPASS", ""), "nekonn.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "nekonn.png"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "nekonn.png"),
        ]
        ico_candidates = [
            os.path.join(getattr(sys, "_MEIPASS", ""), "assets", "nekonn.ico"),
            os.path.join(getattr(sys, "_MEIPASS", ""), "nekonn.ico"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "nekonn.ico"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "nekonn.ico"),
        ]
        def _apply_icon():
            for p in png_candidates:
                if os.path.exists(p):
                    try:
                        self._icon_photo = tk.PhotoImage(file=p)
                        self.iconphoto(True, self._icon_photo)
                        break
                    except Exception:
                        pass
            for p in ico_candidates:
                if os.path.exists(p):
                    try:
                        self.iconbitmap(p)
                        break
                    except Exception:
                        pass
        _apply_icon()
        self.after(200, _apply_icon)

        # ── 共享状态 ─────────────────────────────────
        self._cookie     = None
        self._student_id = None
        self._courses    = []
        self._busy       = False        # 防止重复点击
        self._radar_running = False
        self._number_running = False

        # ── 日志缓冲 ─────────────────────────────────
        self._log_lines  = []

        # ── 根布局：顶栏 + 内容区 ─────────────────────
        self._build_topbar()
        self._content = ctk.CTkFrame(self, fg_color=BG)
        self._content.pack(fill="both", expand=True, padx=0, pady=0)

        # ── 日志抽屉（隐藏态，覆盖在内容区上方）────────
        self._log_drawer_visible = False
        self._build_log_drawer()

        # ── 初始页面 ──────────────────────────────────
        self._show_home()

    # ─────────────────────────────────────────────────────
    # 顶栏
    # ─────────────────────────────────────────────────────
    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=SURFACE, height=48, corner_radius=0)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        make_label(bar, "❤ zako", size=15, color=ACCENT, bold=True).pack(
            side="left", padx=16
        )
        ctk.CTkButton(
            bar, text="📋 日志", width=72, height=30,
            fg_color=SURFACE2, hover_color="#2E2C3F", text_color=TEXT_SEC,
            font=("Microsoft YaHei", 12), corner_radius=8,
            command=self._toggle_log_drawer,
        ).pack(side="right", padx=12, pady=9)

    # ─────────────────────────────────────────────────────
    # 日志抽屉
    # ─────────────────────────────────────────────────────
    def _build_log_drawer(self):
        self._drawer = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        # 不 pack，靠 place 覆盖
        self._log_text = ctk.CTkTextbox(
            self._drawer,
            fg_color="#0A0912", text_color=TEXT_SEC,
            font=("Courier New", 11),
            wrap="word", state="disabled",
            corner_radius=8,
        )
        self._log_text.pack(fill="both", expand=True, padx=12, pady=(8, 12))

        ctk.CTkButton(
            self._drawer, text="✕ 关闭日志", width=120, height=28,
            fg_color=SURFACE2, hover_color=ACCENT_DK, text_color=TEXT_SEC,
            font=("Microsoft YaHei", 12), corner_radius=8,
            command=self._toggle_log_drawer,
        ).pack(pady=(0, 8))

    def _toggle_log_drawer(self):
        if self._log_drawer_visible:
            self._drawer.place_forget()
            self._log_drawer_visible = False
        else:
            self._drawer.place(relx=0, rely=0.08, relwidth=1, relheight=0.92)
            self._log_drawer_visible = True

    def _log(self, msg: str):
        """线程安全的日志写入（可从任意线程调用）。"""
        self._log_lines.append(msg)
        print(msg)
        self.after(0, self._flush_log, msg)

    def _flush_log(self, msg: str):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ─────────────────────────────────────────────────────
    # 内容区切换（清空再重建）
    # ─────────────────────────────────────────────────────
    def _clear_content(self):
        for w in self._content.winfo_children():
            w.destroy()

    # =======================================================
    # 第 1 页：主页  ——  猫爪按钮
    # =======================================================
    def _show_home(self):
        self._clear_content()
        f = self._content

        ctk.CTkFrame(f, fg_color=BG, height=60).pack()

        make_label(f, "zako 签到助手", size=26, bold=True, anchor="center").pack()
        make_label(f, "点击猫爪，开始喵~", size=13, color=TEXT_SEC, anchor="center").pack(pady=(4, 0))

        ctk.CTkFrame(f, fg_color=BG, height=44).pack()

        # 猫爪按钮主体
        paw_frame = ctk.CTkFrame(
            f, fg_color=SURFACE, width=180, height=180, corner_radius=90
        )
        paw_frame.pack()
        paw_frame.pack_propagate(False)

        paw_lbl = ctk.CTkLabel(
            paw_frame, text="🐾", font=("Segoe UI Emoji", 80), fg_color="transparent"
        )
        paw_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # 点击 / 悬停效果
        def on_enter(e):
            if not self._busy:
                paw_frame.configure(fg_color="#2A1F35")
        def on_leave(e):
            paw_frame.configure(fg_color=SURFACE)
        def on_click(e):
            if not self._busy:
                self._start_login()

        for w in (paw_frame, paw_lbl):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)

        ctk.CTkFrame(f, fg_color=BG, height=24).pack()

        # 状态文字（动态更新）
        self._home_status = make_label(
            f, "", size=12, color=TEXT_SEC, anchor="center"
        )
        self._home_status.pack()

        ctk.CTkFrame(f, fg_color=BG, height=20).pack()

        make_label(
            f, "厦大 CAS 畅课签到码查询工具 ❤",
            size=11, color=TEXT_SEC, anchor="center"
        ).pack(side="bottom", pady=16)

    def _set_home_status(self, msg, color=TEXT_SEC):
        self.after(0, lambda: self._home_status.configure(text=msg, text_color=color))

    # ── 第1步：启动登录流程 ──────────────────────────────
    def _start_login(self):
        self._busy = True
        self._set_home_status("正在启动浏览器，请稍候喵~❤")

        def on_done(result, err):
            if err or result is None:
                self._log(f"❌ 登录异常: {err}")
                self._busy = False
                self._set_home_status("❌ 出错了，再试一次喵~", DANGER)
                return

            cookie, student_id = result
            if not cookie or not student_id:
                self._log("❌ 未能获取凭证或学生ID")
                self._busy = False
                self._set_home_status("❌ 未能获取凭证，再试一次喵~", DANGER)
                return

            self._cookie     = cookie
            self._student_id = student_id
            self._set_home_status("✅ 凭证就绪！正在拉取课程喵~", SUCCESS)
            self._log("✅ 凭证获取成功，开始拉取课程列表...")

            # 第2步：拉取学期信息 + 课程列表（同步，放子线程）
            def fetch_courses():
                s_id, y_id = get_current_semester_info(cookie, self._log)
                return get_courses(cookie, s_id, y_id, self._log)

            def on_courses(courses, err2):
                self._busy = False
                if err2 or not courses:
                    self._log(f"❌ 课程拉取失败: {err2}")
                    self._set_home_status("❌ 课程列表拉取失败喵哦~", DANGER)
                    return
                self._courses = courses
                self._log(f"🎉 成功拉取到 {len(courses)} 门课程喵！即将载入课程列表喵~❤")
                self._set_home_status(f"🎉 成功获取 {len(courses)} 门课程喵~❤", SUCCESS)
                self.after(500, self._show_courses)

            run_sync_in_thread(fetch_courses, on_courses)

        run_async(login_and_get_cookie(log=self._log), on_done)

    # =======================================================
    # 第 2 页：课程列表
    # =======================================================
    def _auto_scan(self):
        """一键扫描所有课程，检测签到并自动执行"""
        if self._busy:
            return
        if not self._courses:
            self._log("⚠️ 课程列表为空，主人是不是忘记登录了呜")
            return

        self._busy = True
        self._log("🚀 开始查看所有课程，上班了呜呜呜...")

        # 弹出一个扫描中的提示页面
        self._clear_content()
        f = self._content

        hdr = ctk.CTkFrame(f, fg_color=BG)
        hdr.pack(fill="x", padx=20, pady=(16, 8))
        make_label(hdr, "🚀 自动扫描中", size=24, bold=True).pack(anchor="w")
        make_label(hdr, "正在依次检查每门课程的签到状态，请稍候喵...",
                   size=12, color=TEXT_SEC).pack(anchor="w", pady=(2, 0))
        separator(f).pack(fill="x", padx=20, pady=6)

        # 进度条
        bar_frame = ctk.CTkFrame(f, fg_color=BG)
        bar_frame.pack(fill="x", padx=20, pady=6)
        self._scan_bar = ctk.CTkProgressBar(
            bar_frame, width=460, mode="indeterminate",
            progress_color=ACCENT, fg_color=SURFACE2
        )
        self._scan_bar.pack()
        self._scan_bar.start()

        # 结果滚动区
        self._scan_log_box = ctk.CTkTextbox(
            f, width=460, height=380,
            fg_color="#0A0912", text_color=TEXT_SEC,
            font=("Courier New", 11), wrap="word", state="disabled",
            corner_radius=10,
        )
        self._scan_log_box.pack(padx=20, pady=6, fill="both", expand=True)

        # 后台线程执行扫描
        def work():
            return auto_scan_all_courses(
                self._cookie, self._student_id, self._courses,
                log=self._scan_log
            )

        def on_done(result, err):
            self._busy = False
            self.after(0, self._finish_auto_scan, result, err)

        run_sync_in_thread(work, on_done)

    def _scan_log(self, msg):
        """扫描日志：同时写入全局日志和扫描窗口"""
        self._log(msg)
        self.after(0, self._append_scan_log, msg)

    def _append_scan_log(self, msg):
        box = getattr(self, "_scan_log_box", None)
        if box is None:
            return
        try:
            if not box.winfo_exists():
                return
        except Exception:
            return
        box.configure(state="normal")
        box.insert("end", msg + "\n")
        box.see("end")
        box.configure(state="disabled")

    def _finish_auto_scan(self, results, err):
        # 停止进度条
        bar = getattr(self, "_scan_bar", None)
        if bar is not None:
            try:
                bar.stop()
            except Exception:
                pass

        if err:
            self._log(f"❌ 自动扫描出错: {err}")
            self._scan_log(f"❌ 出错: {err}")
            return

        # 统计
        total = len(results)
        success = sum(1 for r in results if r.get("ok"))
        self._scan_log(f"\n{'=' * 40}")
        self._scan_log(f"✅ 扫描完成！共 {total} 门课程，成功签到 {success} 次")

        # 显示“返回”按钮
        back = ctk.CTkButton(
            self._content, text="← 返回课程列表", width=200, height=40,
            fg_color=ACCENT, hover_color=ACCENT_DK, text_color=BG,
            font=("Microsoft YaHei", 13, "bold"), corner_radius=10,
            command=self._show_courses,
        )
        back.pack(pady=10)

    def _make_course_row(self, parent, course):
        name = course.get("display_name") or course.get("name") or "未知课程"
        cid = course.get("id")

        row = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        row.pack(fill="x", pady=5, padx=4)

        icon = ctk.CTkLabel(
            row, text="📚", font=("Segoe UI Emoji", 22),
            width=44, height=44, fg_color=SURFACE2, corner_radius=10
        )
        icon.pack(side="left", padx=(10, 8), pady=10)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, pady=10)

        ctk.CTkLabel(
            info, text=name,
            font=("Microsoft YaHei", 13, "bold"),
            text_color=TEXT_PRI, anchor="w"
        ).pack(anchor="w")

        ctk.CTkLabel(
            info, text=f"ID: {cid}",
            font=("Courier New", 11),
            text_color=TEXT_SEC, anchor="w"
        ).pack(anchor="w")

        arrow = ctk.CTkLabel(row, text="›", font=("Arial", 22), text_color=TEXT_SEC)
        arrow.pack(side="right", padx=12)

        # 点击进入签到查询
        def on_click(e, _cid=cid, _name=name):
            self._show_code(_cid, _name)

        def on_enter(e):
            row.configure(fg_color=SURFACE2)

        def on_leave(e):
            row.configure(fg_color=SURFACE)

        for w in (row, icon, info, arrow):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

    def _show_courses(self):
        self._log("DEBUG: _show_courses 被调用了")
        self._clear_content()
        f = self._content

        hdr = ctk.CTkFrame(f, fg_color=BG)
        hdr.pack(fill="x", padx=20, pady=(16, 8))

        # 返回按钮
        back_btn = ctk.CTkButton(
            hdr, text="← 返回主页", width=80, height=28,
            fg_color=SURFACE2, hover_color=SURFACE, text_color=TEXT_SEC,
            font=("Microsoft YaHei", 12), corner_radius=8,
            command=self._show_home,
        )
        back_btn.pack(anchor="w", pady=(0, 10))

        # ↓↓↓ 新增：一键自动扫描按钮 ↓↓↓
        scan_btn = ctk.CTkButton(
            hdr, text="🚀 一键扫描并自动签到", width=200, height=36,
            fg_color=ACCENT, hover_color=ACCENT_DK, text_color=BG,
            font=("Microsoft YaHei", 13, "bold"), corner_radius=10,
            command=self._auto_scan,
        )
        scan_btn.pack(anchor="w", pady=(0, 10))
        # ↑↑↑ 新增结束 ↑↑↑

        make_label(hdr, "选择课程", size=24, bold=True).pack(anchor="w")
        # 标题区
        hdr = ctk.CTkFrame(f, fg_color=BG)
        hdr.pack(fill="x", padx=20, pady=(16, 8))

        # ↓↓↓ 绝对原位插入：仅在此处新增一个返回按钮，其他排版代码1个字都不变 ↓↓↓
        back_btn = ctk.CTkButton(
            hdr, text="← 返回主页", width=80, height=28,
            fg_color=SURFACE2, hover_color=SURFACE, text_color=TEXT_SEC,
            font=("Microsoft YaHei", 12), corner_radius=8,
            command=self._show_home,
        )
        back_btn.pack(anchor="w", pady=(0, 10))
        # ↑↑↑ 插入结束 ↑↑↑

        make_label(hdr, "选择课程", size=24, bold=True).pack(anchor="w")
        make_label(
            hdr, f"共 {len(self._courses)} 门课，点击查看最新签到码",
            size=12, color=TEXT_SEC
        ).pack(anchor="w", pady=(2, 0))

        separator(f).pack(fill="x", padx=20, pady=4)

        # 可滚动课程列表
        scroll = ctk.CTkScrollableFrame(f, fg_color=BG, scrollbar_button_color=SURFACE2)
        scroll.pack(fill="both", expand=True, padx=12, pady=4)

        for course in self._courses:
            self._make_course_row(scroll, course)

    # =======================================================
    # 第 3 页：签到结果（数字 / 雷达统一判定）
    # =======================================================
    def _show_code(self, course_id, course_name):
        self._clear_content()
        f = self._content

        # 顶部：返回按钮 + 课程名
        hdr = ctk.CTkFrame(f, fg_color=BG)
        hdr.pack(fill="x", padx=12, pady=(14, 4))

        back_btn = ctk.CTkButton(
            hdr, text="← 返回", width=72, height=32,
            fg_color=SURFACE2, hover_color=SURFACE, text_color=TEXT_SEC,
            font=("Microsoft YaHei", 12), corner_radius=8,
            command=self._show_courses,
        )
        back_btn.pack(side="left")

        make_label(
            hdr, text=course_name, size=14, bold=True,
            color=TEXT_PRI, anchor="w", wraplength=330
        ).pack(side="left", padx=10)

        separator(f).pack(fill="x", padx=20, pady=6)

        # 结果卡片容器（先放 loading）
        self._code_card_frame = ctk.CTkFrame(f, fg_color=BG)
        self._code_card_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self._show_loading_card()

        # 后台拉取最新签到记录，并统一判定类型
        def fetch():
            latest = get_latest_rollcall(course_id, self._cookie, self._student_id)
            if latest is None:
                return None
            rid = str(latest.get("id") or latest.get("rollcall_id") or "")
            t = fmt_time(latest.get("rollcall_time") or latest.get("created_at"))

            # 先判断是否为雷达签到
            is_radar = (
                    bool(latest.get("is_radar"))
                    or bool(latest.get("isRadar"))
                    or "radar" in str(latest.get("type", "")).lower()
            )
            if is_radar:
                active = find_active_radar_record(self._cookie, rid, self._log)
                if active is not None or str(latest.get("status") or "") == "active":
                    return {"type": "radar_active", "rid": rid, "time": t}
                return {"type": "radar_past", "time": t}

            # 如果是数字签到（包括已结束的）
            if latest.get("is_number"):
                # 尝试获取 number_code（可能为 None）
                number_code, status, _ = get_number_code(rid, self._cookie)
                # 如果 status 为空，从 latest 中获取
                if not status:
                    status = latest.get("status", "finished")
                return {
                    "type": "digital",
                    "code": number_code,  # 可能为 None
                    "status": status,
                    "time": t,
                    "rid": rid,
                }

            # 其他情况（如 GPS、扫码等）
            return {"type": "other", "time": t}

        def on_result(result, err):
            if err:
                self._log(f"❌ 查询出错: {err}")
                self.after(0, self._show_result_card, None, course_id, course_name)
                return
            self._log(
                f"✅ {course_name} | {result['time'] if result else '-'} "
                f"| 类型: {result['type'] if result else '无'}"
            )
            self.after(0, self._show_result_card, result, course_id, course_name)

        run_sync_in_thread(fetch, on_result)

    def _clear_card_frame(self):
        def _stop_bars(parent):
            for child in parent.winfo_children():
                if isinstance(child, ctk.CTkProgressBar):
                    try:
                        child.stop()
                    except Exception:
                        pass
                _stop_bars(child)
        _stop_bars(self._code_card_frame)
        for w in self._code_card_frame.winfo_children():
            w.destroy()

    def _show_loading_card(self):
        self._clear_card_frame()
        card = ctk.CTkFrame(self._code_card_frame, fg_color=SURFACE, corner_radius=20)
        card.pack(fill="both", expand=True)
        ctk.CTkLabel(
            card, text="🔍", font=("Segoe UI Emoji", 48)
        ).place(relx=0.5, rely=0.4, anchor="center")
        ctk.CTkLabel(
            card, text="正在查询签到喵~",
            font=("Microsoft YaHei", 14), text_color=TEXT_SEC
        ).place(relx=0.5, rely=0.56, anchor="center")
        ctk.CTkProgressBar(
            card, width=200, mode="indeterminate",
            progress_color=ACCENT, fg_color=SURFACE2
        ).place(relx=0.5, rely=0.68, anchor="center")
        # 启动动画
        for w in card.winfo_children():
            if isinstance(w, ctk.CTkProgressBar):
                w.start()

    def _show_result_card(self, result, course_id, course_name):
        self._clear_card_frame()

        card = ctk.CTkFrame(self._code_card_frame, fg_color=SURFACE, corner_radius=20)
        card.pack(fill="both", expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        if result is None:
            # 无签到记录
            ctk.CTkLabel(inner, text="😿", font=("Segoe UI Emoji", 52)).pack()
            make_label(inner, "暂无签到记录", size=18, bold=True, anchor="center").pack(pady=(8,2))
            make_label(inner, "这门课还没有签到喵~", size=13, color=TEXT_SEC, anchor="center").pack()

        elif result["type"] == "digital":
            # 有数字签到码
            status_map   = {"active": ("✅ 进行中", SUCCESS), "finished": ("🔒 已结束", TEXT_SEC)}
            status_txt, status_clr = status_map.get(result["status"], (result["status"], TEXT_SEC))

            ctk.CTkLabel(inner, text="🐾", font=("Segoe UI Emoji", 46)).pack()
            make_label(inner, "签到码", size=13, color=TEXT_SEC, anchor="center").pack(pady=(4,0))

            # 大号签到码（可选中复制）
            code_entry = ctk.CTkEntry(
                inner, width=240, height=80,
                font=("Arial Black", 48),
                text_color=ACCENT, fg_color="transparent",
                border_width=0, justify="center",
            )
            code_entry.insert(0, str(result["code"]))
            code_entry.configure(state="readonly")
            code_entry.pack(pady=4)

            # 状态标签
            status_frame = ctk.CTkFrame(inner, fg_color=SURFACE2, corner_radius=20)
            status_frame.pack(pady=4)
            ctk.CTkLabel(
                status_frame, text=status_txt,
                font=("Microsoft YaHei", 12, "bold"),
                text_color=status_clr
            ).pack(padx=16, pady=5)

            make_label(
                inner, f"签到时间：{result['time']}",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack(pady=(6, 0))

            if result["status"] == "active":
                make_button(
                    inner, "🐾 一键数字签到",
                    command=lambda: self._start_number(
                        result["rid"], course_id, course_name
                    ),
                    width=240, height=46, size=14
                ).pack(pady=(14, 0))
            else:
                make_label(
                    inner, "签到已结束，无需提交喵~",
                    size=12, color=TEXT_SEC, anchor="center"
                ).pack(pady=(10, 0))

        elif result["type"] == "radar_active":
            # 雷达签到正在进行
            ctk.CTkLabel(inner, text="📡", font=("Segoe UI Emoji", 52)).pack()
            make_label(inner, "雷达签到进行中喵❤", size=18, bold=True, anchor="center").pack(pady=(8,2))
            make_label(
                inner, "教师在实时广播位置，点击按钮自动定位签到喵~",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack()
            make_label(
                inner, f"签到时间：{result['time']}",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack(pady=(6, 0))
            make_button(
                inner, "🛰 一键雷达签到",
                command=lambda: self._start_radar(result["rid"], course_name),
                width=240, height=46, size=14
            ).pack(pady=(14, 0))

        elif result["type"] == "radar_past":
            # 只有历史雷达签到记录
            ctk.CTkLabel(inner, text="📡", font=("Segoe UI Emoji", 52)).pack()
            make_label(inner, "上一次是雷达签到喵❤", size=18, bold=True, anchor="center").pack(pady=(8,2))
            make_label(
                inner, "当前没有进行中的雷达签到喵~",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack()
            make_label(
                inner, f"签到时间：{result['time']}",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack(pady=(6, 0))

        else:
            # 无数字签到码（GPS/扫码等）
            ctk.CTkLabel(inner, text="📍", font=("Segoe UI Emoji", 52)).pack()
            make_label(inner, "无数字签到码", size=18, bold=True, anchor="center").pack(pady=(8,2))
            make_label(
                inner, "可能是 GPS / 扫码等其他签到方式喵~",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack()
            make_label(
                inner, f"签到时间：{result['time']}",
                size=12, color=TEXT_SEC, anchor="center"
            ).pack(pady=(6, 0))

        # 再查一次按钮
        make_button(
            self._code_card_frame, "🔄 再查一次",
            command=lambda: self._show_code(course_id, course_name),
            width=300, height=42
        ).pack(pady=(12, 4))

    # =======================================================
    # 第 4 页：雷达签到执行视图
    # =======================================================
    def _start_radar(self, rollcall_id, course_name):
        if self._radar_running:
            return
        self._radar_running = True
        self._log(f"🛰 主人点击了雷达签到喵❤ rollcall_id={rollcall_id}")

        self._clear_card_frame()

        card = ctk.CTkFrame(self._code_card_frame, fg_color=SURFACE, corner_radius=20)
        card.pack(fill="both", expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="🛰", font=("Segoe UI Emoji", 46)).pack()
        self._radar_status = make_label(
            inner, "正在四校区定位教师位置喵~请稍候...",
            size=13, color=TEXT_SEC, anchor="center"
        )
        self._radar_status.pack(pady=(6, 8))

        self._radar_bar = ctk.CTkProgressBar(
            inner, width=220, mode="indeterminate",
            progress_color=ACCENT, fg_color=SURFACE2
        )
        self._radar_bar.pack(pady=(0, 10))
        self._radar_bar.start()

        self._radar_log_box = ctk.CTkTextbox(
            inner, width=380, height=150,
            fg_color="#0A0912", text_color=TEXT_SEC,
            font=("Courier New", 11), wrap="word", state="disabled",
            corner_radius=8,
        )
        self._radar_log_box.pack()

        def work():
            return send_radar(self._cookie, rollcall_id, log=self._radar_log)

        def on_done(result, err):
            if err:
                self.after(0, self._finish_radar, False, {"error": str(err)}, rollcall_id, course_name)
                return
            ok, info = result
            self.after(0, self._finish_radar, ok, info, rollcall_id, course_name)

        run_sync_in_thread(work, on_done)

    def _radar_log(self, msg):
        self._log(msg)
        self.after(0, self._append_radar_log, msg)

    def _append_radar_log(self, msg):
        box = getattr(self, "_radar_log_box", None)
        if box is None:
            return
        try:
            if not box.winfo_exists():
                return
        except Exception:
            return
        box.configure(state="normal")
        box.insert("end", msg + "\n")
        box.see("end")
        box.configure(state="disabled")

    def _finish_radar(self, ok, info, rollcall_id, course_name):
        self._radar_running = False

        status = getattr(self, "_radar_status", None)
        bar = getattr(self, "_radar_bar", None)
        if status is None or bar is None:
            return
        try:
            if not status.winfo_exists():
                return
        except Exception:
            return
        try:
            bar.stop()
        except Exception:
            pass

        if ok:
            campus = info.get("campus") or ""
            detail = f"（{campus}）" if campus else ""
            pos = info.get("position")
            if pos:
                detail += f" 位置≈({pos[0]:.5f}, {pos[1]:.5f})"
            status.configure(text=f"✅ 雷达签到成功喵❤ {detail}", text_color=SUCCESS)
        else:
            status.configure(text="❌ 雷达签到失败喵，请重试~", text_color=DANGER)
            make_button(
                self._code_card_frame, "🔄 再试一次",
                command=lambda: self._start_radar(rollcall_id, course_name),
                width=300, height=42
            ).pack(pady=(12, 4))

    def _start_number(self, rollcall_id, course_id, course_name):
        if self._number_running:
            return
        self._number_running = True
        self._log(f"🐾 主人点击了数字签到喵❤ rollcall_id={rollcall_id}")

        self._clear_card_frame()

        card = ctk.CTkFrame(self._code_card_frame, fg_color=SURFACE, corner_radius=20)
        card.pack(fill="both", expand=True)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="🐾", font=("Segoe UI Emoji", 46)).pack()
        self._number_status = make_label(
            inner, "正在提交数字签到喵~请稍候...",
            size=13, color=TEXT_SEC, anchor="center"
        )
        self._number_status.pack(pady=(6, 8))

        self._number_bar = ctk.CTkProgressBar(
            inner, width=220, mode="indeterminate",
            progress_color=ACCENT, fg_color=SURFACE2
        )
        self._number_bar.pack(pady=(0, 10))
        self._number_bar.start()

        def work():
            return submit_number_code(self._cookie, rollcall_id, log=self._log)

        def on_done(result, err):
            if err:
                self.after(0, self._finish_number, False, {"reason": "exception", "error": str(err)}, rollcall_id, course_id, course_name)
                return
            ok, info = result
            self.after(0, self._finish_number, ok, info, rollcall_id, course_id, course_name)

        run_sync_in_thread(work, on_done)

    def _finish_number(self, ok, info, rollcall_id, course_id, course_name):
        self._number_running = False

        status = getattr(self, "_number_status", None)
        bar = getattr(self, "_number_bar", None)
        if status is None or bar is None:
            return
        try:
            if not status.winfo_exists():
                return
        except Exception:
            return
        try:
            bar.stop()
        except Exception:
            pass

        if ok:
            code = info.get("code") or ""
            status.configure(text=f"✅ 数字签到成功喵❤ 签到码：{code}", text_color=SUCCESS)
        else:
            reason = info.get("reason")
            if reason == "finished":
                status.configure(text="⏰ 签到已结束，无法提交喵~", text_color=DANGER)
            elif reason == "no_code":
                status.configure(text="❌ 未获取到签到码喵，请再查一次~", text_color=DANGER)
            else:
                status.configure(text="❌ 数字签到失败喵，请重试~", text_color=DANGER)
            make_button(
                self._code_card_frame, "🔄 再试一次",
                command=lambda: self._start_number(rollcall_id, course_id, course_name),
                width=300, height=42
            ).pack(pady=(12, 4))
        make_button(
            self._code_card_frame, "↩ 返回签到码",
            command=lambda: self._show_code(course_id, course_name),
            width=300, height=42
        ).pack(pady=(8, 4))
def run_cli_mode():
    """无 GUI 环境下的命令行模式"""
    print("🖥 无 UI 环境，进入命令行模式")
    print("=" * 50)

    # 1. 登录获取 Cookie
    cookie, student_id = asyncio.run(login_and_get_cookie(log=print))

    if not cookie or not student_id:
        print("❌ 登录失败了呜呜")
        sys.exit(1)

    print(f"✅ 找到主人的学生ID: {student_id}了！")

    # 2. 获取学期 + 课程
    try:
        s_id, y_id = get_current_semester_info(cookie, log=print)
        courses = get_courses(cookie, s_id, y_id, log=print)
    except Exception as e:
        print(f"❌ 拉取课程失败了呜呜呜: {e}")
        sys.exit(1)

    if not courses:
        print("⚠️ 主人的学期课表似乎是空的？")
        sys.exit(0)

    print(f"✅ 共 {len(courses)} 门课程，准备开始自动扫描签到喵！")

    # 3. 自动扫描
    try:
        results = auto_scan_all_courses(cookie, student_id, courses, log=print)
    except Exception as e:
        print(f"❌ 扫描出错了qwq: {e}")
        sys.exit(1)

    # 4. 汇总
    success = sum(1 for r in results if r.get("ok"))
    print("\n" + "=" * 50)
    print(f"🎉 完成啦！共 {len(courses)} 门课，成功签到 {success} 次")
    sys.exit(0)

# ==============================================================================
# 入口
# ==============================================================================
if __name__ == "__main__":
    if is_headless_env():
        run_cli_mode()
    else:
        app = ZakoApp()
        app.mainloop()
