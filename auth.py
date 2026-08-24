"""
auth.py
========
โมดูล Login + สิทธิ์พนักงานตามตำแหน่ง (Role-Based Access Control)
สำหรับต่อเข้ากับ main.py เดิมของระบบ Kelyfos Company OS

วิธีติดตั้ง:
1. วางไฟล์นี้ไว้ในโฟลเดอร์เดียวกับ main.py
2. ตั้งค่า Environment Variable บน Render:
   - SESSION_SECRET_KEY = (สุ่มตัวอักษรยาวๆ ให้เป็นความลับ)
   - DEFAULT_OWNER_PASSWORD = (รหัสผ่านเริ่มต้นของบัญชี owner คนแรก)
3. ใน main.py เพิ่ม:
     from auth import (
         auth_router, get_current_employee, require_roles,
         ROLES, EMPLOYEES_DB, load_employees_from_sheet, GAS_WEB_APP_URL as AUTH_GAS_URL
     )
     app.include_router(auth_router)
4. เพิ่มการเช็คสิทธิ์ที่ต้นทุกฟังก์ชัน route เดิม (ดูตัวอย่างท้ายไฟล์ / คู่มือ integration)
5. เพิ่ม action ใหม่ใน Google Apps Script ของคุณ (ดูไฟล์ apps_script_employees.gs ที่แนบมาด้วย)
"""

import os
import hmac
import hashlib
import base64
import time
import secrets
from typing import Optional, List

import requests
from fastapi import APIRouter, Request, Form, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

# ----------------------------------------------------
# ตั้งค่าพื้นฐาน
# ----------------------------------------------------
SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
if not SECRET_KEY:
    # กันลืมตั้งค่าบน Render — ถ้าไม่ตั้งจะ generate แบบสุ่มทุกครั้งที่ restart
    # (แปลว่า session จะหลุดทุกครั้งที่ deploy ใหม่ — ควรตั้งค่าจริงบน Render Environment)
    SECRET_KEY = secrets.token_hex(32)
    print("[WARNING] ไม่พบ SESSION_SECRET_KEY ใน Environment Variables — "
          "ใช้ค่าสุ่มชั่วคราว (session จะหลุดทุกครั้งที่ redeploy) "
          "กรุณาตั้งค่า SESSION_SECRET_KEY บน Render ทันที")

SESSION_MAX_AGE_SECONDS = 60 * 60 * 8  # 8 ชั่วโมง

# ใช้ GAS_WEB_APP_URL ตัวเดียวกับใน main.py — ใส่ URL เดียวกันตรงนี้ด้วย
# (แนะนำให้ import ค่าจาก main.py แทนการ hardcode ซ้ำ ถ้า import วนกันไม่ได้ ให้ตั้งผ่าน env var)
GAS_WEB_APP_URL = os.environ.get(
    "GAS_WEB_APP_URL",
    "https://script.google.com/macros/s/AKfycbzINU5Sgd9OwT5XI2VpgP04YZCDBr2jPXT1k9VzWpdrXq5i_LILDOW_JohOIqVW6b_t/exec"
)

# ----------------------------------------------------
# ตำแหน่งงานในบริษัท (Role Definitions)
# ----------------------------------------------------
ROLES = {
    "owner":         "เจ้าของ / ผู้บริหาร",
    "admin":         "แอดมิน (ดูแล AI แชทลูกค้า)",
    "designer":      "นักออกแบบ",
    "price_analyst": "นักวิเคราะห์ราคา",
    "warehouse":     "พนักงานคลังและจัดซื้อ",
    "foreman":       "โฟร์แมน",
    "qc":            "ผู้ตรวจงาน (QC)",
}

# กำหนดว่าแต่ละ "หน้า/โมดูล" ใครเข้าได้บ้าง — ใช้ตอนต่อ require_roles(...) ในแต่ละ route
# แก้ไข/เพิ่มได้ตามจริงภายหลัง
PAGE_PERMISSIONS = {
    "dashboard":          ["owner", "admin"],
    "chat_monitor":        ["owner", "admin"],
    "admin_settings":      ["owner", "admin"],
    "employee_management": ["owner"],
    # โมดูลในอนาคต (เผื่อไว้ล่วงหน้า):
    "design_module":       ["owner", "designer"],
    "price_module":        ["owner", "price_analyst", "admin"],
    "warehouse_module":     ["owner", "warehouse"],
    "site_module":          ["owner", "foreman"],
    "qc_module":            ["owner", "qc"],
}

# ----------------------------------------------------
# In-memory DB ของพนักงาน (sync กับ Google Sheets)
# ----------------------------------------------------
EMPLOYEES_DB: List[dict] = []


def load_employees_from_sheet():
    """โหลดรายชื่อพนักงานจาก Google Sheets ผ่าน Apps Script (action=get_employees)"""
    global EMPLOYEES_DB
    try:
        resp = requests.get(GAS_WEB_APP_URL, params={"action": "get_employees"}, timeout=10)
        if resp.status_code != 200:
            return
        rows = resp.json()
        loaded = []
        for idx, row in enumerate(rows[1:], start=1):  # แถวแรกเป็น header
            if not row or all(not str(cell).strip() for cell in row):
                continue
            try:
                e_id = int(row[0]) if str(row[0]).strip().isdigit() else idx
            except Exception:
                e_id = idx
            username = str(row[1]).strip() if len(row) > 1 else f"user{e_id}"
            password_hash = str(row[2]) if len(row) > 2 else ""
            full_name = str(row[3]) if len(row) > 3 else username
            role = str(row[4]).strip() if len(row) > 4 and str(row[4]).strip() in ROLES else "admin"
            status = str(row[5]).strip() if len(row) > 5 and str(row[5]).strip() else "active"

            loaded.append({
                "id": e_id,
                "username": username,
                "password_hash": password_hash,
                "full_name": full_name,
                "role": role,
                "status": status,
            })
        if loaded:
            EMPLOYEES_DB = loaded
    except Exception as e:
        print(f"[auth] Error loading employees from sheet: {e}")

    # ถ้ายังไม่มีพนักงานเลย (ครั้งแรกที่รันระบบ) ให้สร้างบัญชี owner เริ่มต้นอัตโนมัติ
    if not EMPLOYEES_DB:
        default_pw = os.environ.get("DEFAULT_OWNER_PASSWORD", "changeme123")
        owner = {
            "id": 1,
            "username": "owner",
            "password_hash": hash_password(default_pw),
            "full_name": "เจ้าของระบบ",
            "role": "owner",
            "status": "active",
        }
        EMPLOYEES_DB.append(owner)
        sync_employee_to_sheet(owner)
        print(f"[auth] สร้างบัญชี owner เริ่มต้นแล้ว: username='owner' "
              f"password='{default_pw}' — กรุณาเปลี่ยนรหัสผ่านทันทีหลัง login ครั้งแรก")


def sync_employee_to_sheet(emp: dict):
    if not GAS_WEB_APP_URL:
        return
    payload = {
        "action": "save_employee",
        "employee_id": emp["id"],
        "username": emp["username"],
        "password_hash": emp["password_hash"],
        "full_name": emp["full_name"],
        "role": emp["role"],
        "status": emp["status"],
    }
    try:
        requests.post(GAS_WEB_APP_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"[auth] Error syncing employee to sheet: {e}")


def delete_employee_from_sheet(employee_id: int):
    if not GAS_WEB_APP_URL:
        return
    try:
        requests.post(GAS_WEB_APP_URL, json={"action": "delete_employee", "employee_id": employee_id}, timeout=10)
    except Exception as e:
        print(f"[auth] Error deleting employee from sheet: {e}")


# ----------------------------------------------------
# Password hashing (PBKDF2 — stdlib เท่านั้น ไม่ต้องเพิ่ม dependency)
# ----------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${pwd_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, hash_hex = stored_hash.split("$")
    except ValueError:
        return False
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return hmac.compare_digest(pwd_hash.hex(), hash_hex)


# ----------------------------------------------------
# Session token (เซ็นด้วย HMAC — เก็บใน cookie, ไม่ต้องมี session store แยก)
# ----------------------------------------------------
def create_session_token(employee_id: int, role: str) -> str:
    expiry = int(time.time()) + SESSION_MAX_AGE_SECONDS
    payload = f"{employee_id}:{role}:{expiry}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(token.encode()).decode()


def verify_session_token(token: str) -> Optional[dict]:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        employee_id, role, expiry, signature = decoded.split(":")
        payload = f"{employee_id}:{role}:{expiry}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        if int(expiry) < time.time():
            return None
        return {"employee_id": int(employee_id), "role": role}
    except Exception:
        return None


def get_current_employee(request: Request) -> Optional[dict]:
    """ดึงข้อมูลพนักงานที่ login อยู่ (คืนค่า None ถ้าไม่ได้ login หรือ session หมดอายุ)"""
    token = request.cookies.get("session_token")
    if not token:
        return None
    data = verify_session_token(token)
    if not data:
        return None
    emp = next((e for e in EMPLOYEES_DB if e["id"] == data["employee_id"]), None)
    if not emp or emp.get("status") != "active":
        return None
    return emp


def require_roles(request: Request, allowed_roles: Optional[List[str]] = None):
    """
    เช็คสิทธิ์สำหรับใช้ต้นทุก route ที่ต้องการป้องกัน
    คืนค่า:
      - dict ของพนักงาน ถ้าผ่านสิทธิ์
      - "unauthenticated" ถ้ายังไม่ login -> ให้ redirect ไป /login
      - "forbidden" ถ้า login แล้วแต่ตำแหน่งไม่มีสิทธิ์ -> ให้ตอบ 403
    """
    emp = get_current_employee(request)
    if emp is None:
        return "unauthenticated"
    if allowed_roles and emp["role"] not in allowed_roles:
        return "forbidden"
    return emp


def auth_guard(request: Request, allowed_roles: Optional[List[str]] = None):
    """
    Helper ใช้ง่ายๆ ต้นแต่ละ route:
        guard = auth_guard(request, PAGE_PERMISSIONS["dashboard"])
        if isinstance(guard, RedirectResponse) or isinstance(guard, HTMLResponse):
            return guard
        employee = guard
    """
    result = require_roles(request, allowed_roles)
    if result == "unauthenticated":
        return RedirectResponse(url="/login", status_code=303)
    if result == "forbidden":
        return HTMLResponse(
            content=_forbidden_page_html(),
            status_code=403,
        )
    return result  # dict ของพนักงาน


def _forbidden_page_html() -> str:
    return """
    <html><head><title>ไม่มีสิทธิ์เข้าถึง</title></head>
    <body style="font-family:sans-serif; text-align:center; padding:80px; color:#334155;">
        <h2>🚫 คุณไม่มีสิทธิ์เข้าถึงหน้านี้</h2>
        <p>ตำแหน่งของคุณไม่ได้รับอนุญาตให้เข้าใช้งานส่วนนี้</p>
        <a href="/" style="color:#2563eb;">กลับหน้าหลัก</a>
    </body></html>
    """


# ----------------------------------------------------
# Router: /login /logout /employees ...
# ----------------------------------------------------
auth_router = APIRouter()

# โหลดพนักงานตอน import โมดูล (เหมือนที่ main.py โหลด ADMINS_DB ตอน start)
load_employees_from_sheet()


def _login_page_html(error: str = "") -> str:
    error_html = f'<p style="color:#dc2626; font-weight:600; margin-top:0;">{error}</p>' if error else ""
    return f"""
    <html>
        <head>
            <title>เข้าสู่ระบบ - Kelyfos Company OS</title>
            <style>
                body {{ font-family:'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background:#f1f5f9;
                        display:flex; align-items:center; justify-content:center; height:100vh; margin:0; color:#334155; }}
                .login-box {{ background:white; padding:40px; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,0.08); width:340px; }}
                h2 {{ margin-top:0; color:#0f172a; font-size:22px; text-align:center; }}
                label {{ font-weight:600; font-size:13px; display:block; margin-top:16px; color:#1e293b; }}
                input {{ width:100%; padding:12px; margin-top:6px; border:1px solid #cbd5e1; border-radius:8px; box-sizing:border-box; font-size:14px; }}
                button {{ width:100%; background:#2563eb; color:white; border:none; padding:12px; border-radius:8px;
                          font-size:15px; font-weight:600; margin-top:22px; cursor:pointer; }}
            </style>
        </head>
        <body>
            <form class="login-box" method="POST" action="/login">
                <h2>🔐 เข้าสู่ระบบ<br><span style="font-size:14px; color:#64748b; font-weight:normal;">Kelyfos Company OS</span></h2>
                {error_html}
                <label>ชื่อผู้ใช้</label>
                <input type="text" name="username" required autofocus>
                <label>รหัสผ่าน</label>
                <input type="password" name="password" required>
                <button type="submit">เข้าสู่ระบบ</button>
            </form>
        </body>
    </html>
    """


@auth_router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _login_page_html()


@auth_router.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    load_employees_from_sheet()
    emp = next((e for e in EMPLOYEES_DB if e["username"] == username), None)
    if not emp or emp.get("status") != "active" or not verify_password(password, emp["password_hash"]):
        return HTMLResponse(content=_login_page_html(error="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"), status_code=401)

    token = create_session_token(emp["id"], emp["role"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,      # Render ใช้ HTTPS อยู่แล้ว
        samesite="lax",
    )
    return response


@auth_router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


# ---------- หน้าจัดการพนักงาน (เฉพาะ owner) ----------

@auth_router.get("/employees", response_class=HTMLResponse)
async def employee_list_page(request: Request):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard

    load_employees_from_sheet()
    rows_html = ""
    for emp in EMPLOYEES_DB:
        status_badge = "🟢 ใช้งานอยู่" if emp["status"] == "active" else "⚪ ปิดใช้งาน"
        rows_html += f"""
        <tr>
            <td>{emp['full_name']}</td>
            <td>{emp['username']}</td>
            <td>{ROLES.get(emp['role'], emp['role'])}</td>
            <td>{status_badge}</td>
            <td>
                <a href="/employees/edit/{emp['id']}" style="color:#2563eb; text-decoration:none; font-weight:600; margin-right:12px;">แก้ไข</a>
                <a href="/employees/delete/{emp['id']}" onclick="return confirm('ลบพนักงานนี้ใช่หรือไม่?');" style="color:#ef4444; text-decoration:none; font-weight:600;">ลบ</a>
            </td>
        </tr>
        """

    return f"""
    <html>
        <head>
            <title>จัดการพนักงาน</title>
            <style>
                body {{ font-family:'Inter', sans-serif; background:#f1f5f9; padding:30px; color:#334155; }}
                .container {{ max-width:1000px; margin:auto; background:white; padding:40px; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,0.05); }}
                table {{ width:100%; border-collapse:collapse; margin-top:20px; }}
                th, td {{ border-bottom:1px solid #e2e8f0; padding:12px; text-align:left; }}
                th {{ background:#f8fafc; font-size:13px; text-transform:uppercase; color:#475569; }}
                .btn-create {{ background:#10b981; color:white; padding:10px 20px; text-decoration:none; border-radius:8px; font-weight:600; font-size:14px; }}
                .header {{ display:flex; justify-content:space-between; align-items:center; }}
                .back-link {{ text-decoration:none; color:#2563eb; font-weight:600; font-size:14px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <a href="/" class="back-link">← กลับหน้าหลัก</a>
                <div class="header" style="margin-top:15px;">
                    <h2 style="margin:0; color:#0f172a;">👥 จัดการพนักงาน</h2>
                    <a href="/employees/create" class="btn-create">+ เพิ่มพนักงานใหม่</a>
                </div>
                <table>
                    <thead><tr><th>ชื่อ</th><th>Username</th><th>ตำแหน่ง</th><th>สถานะ</th><th>การกระทำ</th></tr></thead>
                    <tbody>{rows_html or "<tr><td colspan='5' style='text-align:center; color:#64748b; padding:20px;'>ยังไม่มีพนักงาน</td></tr>"}</tbody>
                </table>
            </div>
        </body>
    </html>
    """


def _employee_form_html(emp: Optional[dict], title: str) -> str:
    emp = emp or {"id": "", "username": "", "full_name": "", "role": "admin", "status": "active"}
    role_options = "".join(
        f'<option value="{key}" {"selected" if emp["role"] == key else ""}>{label}</option>'
        for key, label in ROLES.items()
    )
    status_options = f"""
        <option value="active" {"selected" if emp["status"] == "active" else ""}>ใช้งานอยู่</option>
        <option value="inactive" {"selected" if emp["status"] == "inactive" else ""}>ปิดใช้งาน</option>
    """
    password_note = (
        '<p style="font-size:12px; color:#64748b; margin-top:4px;">เว้นว่างไว้ถ้าไม่ต้องการเปลี่ยนรหัสผ่าน</p>'
        if emp["id"] else ""
    )
    return f"""
    <html>
        <head>
            <title>{title}</title>
            <style>
                body {{ font-family:'Inter', sans-serif; background:#f1f5f9; padding:30px; color:#334155; }}
                .form-box {{ max-width:500px; margin:auto; background:white; padding:40px; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,0.05); }}
                label {{ font-weight:600; margin-top:18px; display:block; font-size:14px; color:#1e293b; }}
                input, select {{ width:100%; padding:12px; margin-top:6px; border:1px solid #cbd5e1; border-radius:8px; box-sizing:border-box; font-size:14px; }}
                .btn-save {{ background:#2563eb; color:white; padding:12px 25px; border:none; border-radius:8px; font-size:15px; cursor:pointer; margin-top:25px; font-weight:600; }}
                .btn-back {{ text-decoration:none; color:#64748b; margin-left:15px; font-weight:500; font-size:14px; }}
            </style>
        </head>
        <body>
            <div class="form-box">
                <h2 style="margin-top:0; color:#0f172a;">{title}</h2>
                <form method="POST" action="/employees/save">
                    <input type="hidden" name="employee_id" value="{emp['id']}">
                    <label>ชื่อ-นามสกุล</label>
                    <input type="text" name="full_name" value="{emp['full_name']}" required>
                    <label>Username</label>
                    <input type="text" name="username" value="{emp['username']}" required>
                    <label>รหัสผ่าน</label>
                    <input type="password" name="password" {"required" if not emp["id"] else ""}>
                    {password_note}
                    <label>ตำแหน่ง</label>
                    <select name="role">{role_options}</select>
                    <label>สถานะ</label>
                    <select name="status">{status_options}</select>
                    <button type="submit" class="btn-save">บันทึก</button>
                    <a href="/employees" class="btn-back">ยกเลิก</a>
                </form>
            </div>
        </body>
    </html>
    """


@auth_router.get("/employees/create", response_class=HTMLResponse)
async def employee_create_page(request: Request):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard
    return _employee_form_html(None, "➕ เพิ่มพนักงานใหม่")


@auth_router.get("/employees/edit/{employee_id}", response_class=HTMLResponse)
async def employee_edit_page(request: Request, employee_id: int):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard
    emp = next((e for e in EMPLOYEES_DB if e["id"] == employee_id), None)
    if not emp:
        return RedirectResponse(url="/employees", status_code=303)
    return _employee_form_html(emp, f"⚙️ แก้ไขพนักงาน: {emp['full_name']}")


@auth_router.post("/employees/save")
async def employee_save(
    request: Request,
    employee_id: str = Form(""),
    username: str = Form(...),
    password: str = Form(""),
    full_name: str = Form(...),
    role: str = Form(...),
    status: str = Form("active"),
):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard

    if role not in ROLES:
        role = "admin"

    # กันชื่อผู้ใช้ซ้ำ
    duplicate = next(
        (e for e in EMPLOYEES_DB if e["username"] == username and str(e["id"]) != employee_id),
        None,
    )
    if duplicate:
        return HTMLResponse("ชื่อผู้ใช้นี้ถูกใช้ไปแล้ว กรุณาย้อนกลับและเลือกชื่ออื่น", status_code=400)

    if employee_id and employee_id.isdigit():
        emp = next((e for e in EMPLOYEES_DB if e["id"] == int(employee_id)), None)
        if emp:
            emp["username"] = username
            emp["full_name"] = full_name
            emp["role"] = role
            emp["status"] = status
            if password.strip():
                emp["password_hash"] = hash_password(password)
            sync_employee_to_sheet(emp)
    else:
        if not password.strip():
            return HTMLResponse("กรุณาระบุรหัสผ่านสำหรับพนักงานใหม่", status_code=400)
        max_id = max([e["id"] for e in EMPLOYEES_DB]) if EMPLOYEES_DB else 0
        new_emp = {
            "id": max_id + 1,
            "username": username,
            "password_hash": hash_password(password),
            "full_name": full_name,
            "role": role,
            "status": status,
        }
        EMPLOYEES_DB.append(new_emp)
        sync_employee_to_sheet(new_emp)

    return RedirectResponse(url="/employees", status_code=303)


@auth_router.get("/employees/delete/{employee_id}")
async def employee_delete(request: Request, employee_id: int):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard

    current_emp = guard
    if current_emp["id"] == employee_id:
        return HTMLResponse("ไม่สามารถลบบัญชีของตัวเองได้", status_code=400)

    global EMPLOYEES_DB
    EMPLOYEES_DB = [e for e in EMPLOYEES_DB if e["id"] != employee_id]
    delete_employee_from_sheet(employee_id)
    return RedirectResponse(url="/employees", status_code=303)
