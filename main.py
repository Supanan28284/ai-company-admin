from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, Query, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
import uvicorn
import os
import datetime
import hmac
import hashlib
import base64
import time
import secrets
from typing import Optional, List
import google.generativeai as genai
import requests

app = FastAPI()

UPLOAD_DIR = "./uploaded_knowledge"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ----------------------------------------------------
# 🔑 ตั้งค่า Gemini API Key และ Google Apps Script URL
#    (อ่านจาก Environment Variables บน Render — ห้าม hardcode คีย์ในโค้ด)
# ----------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("[WARNING] ไม่พบ GEMINI_API_KEY ใน Environment Variables — AI จะทำงานแบบจำลองเท่านั้น "
          "กรุณาตั้งค่าใน Render > Environment")

GAS_WEB_APP_URL = os.environ.get(
    "GAS_WEB_APP_URL",
    "https://script.google.com/macros/s/AKfycbzINU5Sgd9OwT5XI2VpgP04YZCDBr2jPXT1k9VzWpdrXq5i_LILDOW_JohOIqVW6b_t/exec"
)

FB_VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "kelyfos_verify_token_secure")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")

CONNECTED_CHANNELS = [
    {"id": "line_01", "name": "LINE OA: @KelyfosFacade", "status": "เชื่อมต่อแล้ว"},
    {"id": "fb_01", "name": "Facebook Messenger: Kelyfos Official", "status": "เชื่อมต่อแล้ว"},
    {"id": "line_02", "name": "LINE OA: @ModernSignage", "status": "เชื่อมต่อแล้ว"}
]

ADMINS_DB = []
CHAT_SESSIONS_DB = {}
EMPLOYEES_DB: List[dict] = []

# ----------------------------------------------------
# 👥 คำอธิบายและชื่อตำแหน่งพนักงาน AI ภายในบริษัท
# ----------------------------------------------------
AI_ROLES = {
    "admin": {"name": "แอดมินรับแขก / คัดกรองลูกค้า", "desc": "ทักทาย เก็บข้อมูลเบื้องต้น และคัดกรองความต้องการลูกค้า"},
    "estimator": {"name": "นักประเมินราคา (Estimator)", "desc": "คำนวณพื้นที่ ประเมินราคา และค่าใช้จ่ายโครงการจากข้อมูลหน้างาน"},
    "designer": {"name": "นักออกแบบและจัดสเปกวัสดุ", "desc": "เลือกสเปกทางเทคนิค ความหนา สี PVDF และลวดลาย 3D/CNC"},
    "warehouse": {"name": "พนักงานควบคุมสต็อกและคลัง", "desc": "เช็กยอดวัสดุคงเหลือในคลัง ตัดสต็อก และแจ้งเตือนของใกล้หมด"},
    "procurement": {"name": "ฝ่ายจัดซื้อและประสานงาน", "desc": "สรุปรายการสั่งซื้อ สั่งของเพิ่มกับซัพพลายเออร์ และกำหนดส่งมอบ"}
}


# ======================================================
# ======  AUTH / ROLE CONFIG (ระบบ Login + สิทธิ์)  =====
# ======================================================
SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("[WARNING] ไม่พบ SESSION_SECRET_KEY ใน Environment Variables — "
          "ใช้ค่าสุ่มชั่วคราว (session จะหลุดทุกครั้งที่ redeploy) "
          "กรุณาตั้งค่า SESSION_SECRET_KEY บน Render ทันที")

SESSION_MAX_AGE_SECONDS = 60 * 60 * 8  # 8 ชั่วโมง

ROLES = {
    "owner":         "เจ้าของ / ผู้บริหาร",
    "admin":         "แอดมิน (ดูแล AI แชทลูกค้า)",
    "designer":      "นักออกแบบ",
    "price_analyst": "นักวิเคราะห์ราคา",
    "warehouse":     "พนักงานคลังและจัดซื้อ",
    "foreman":       "โฟร์แมน",
    "qc":            "ผู้ตรวจงาน (QC)",
}

PAGE_PERMISSIONS = {
    "dashboard":            ["owner", "admin"],
    "chat_monitor":          ["owner", "admin"],
    "admin_settings":        ["owner", "admin"],
    "employee_management":   ["owner"],
    "design_module":         ["owner", "designer"],
    "price_module":          ["owner", "price_analyst", "admin"],
    "warehouse_module":      ["owner", "warehouse"],
    "site_module":           ["owner", "foreman"],
    "qc_module":             ["owner", "qc"],
}


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


def _forbidden_page_html() -> str:
    return """
    <html><head><title>ไม่มีสิทธิ์เข้าถึง</title></head>
    <body style="font-family:sans-serif; text-align:center; padding:80px; color:#334155;">
        <h2>🚫 คุณไม่มีสิทธิ์เข้าถึงหน้านี้</h2>
        <p>ตำแหน่งของคุณไม่ได้รับอนุญาตให้เข้าใช้งานส่วนนี้</p>
        <a href="/" style="color:#2563eb;">กลับหน้าหลัก</a>
    </body></html>
    """


def auth_guard(request: Request, allowed_roles: Optional[List[str]] = None):
    emp = get_current_employee(request)
    if emp is None:
        return RedirectResponse(url="/login", status_code=303)
    if allowed_roles and emp["role"] not in allowed_roles:
        return HTMLResponse(content=_forbidden_page_html(), status_code=403)
    return emp


def load_employees_from_sheet():
    global EMPLOYEES_DB
    try:
        resp = requests.get(GAS_WEB_APP_URL, params={"action": "get_employees"}, timeout=10)
        if resp.status_code == 200:
            rows = resp.json()
            loaded = []
            for idx, row in enumerate(rows[1:], start=1):
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
                    "id": e_id, "username": username, "password_hash": password_hash,
                    "full_name": full_name, "role": role, "status": status,
                })
            if loaded:
                EMPLOYEES_DB = loaded
    except Exception as e:
        print(f"[auth] Error loading employees from sheet: {e}")

    if not EMPLOYEES_DB:
        default_pw = os.environ.get("DEFAULT_OWNER_PASSWORD", "changeme123")
        owner = {
            "id": 1, "username": "owner", "password_hash": hash_password(default_pw),
            "full_name": "เจ้าของระบบ", "role": "owner", "status": "active",
        }
        EMPLOYEES_DB.append(owner)
        sync_employee_to_sheet(owner)
        print(f"[auth] สร้างบัญชี owner เริ่มต้นแล้ว: username='owner' password='{default_pw}' "
              f"— กรุณาเปลี่ยนรหัสผ่านทันทีหลัง login ครั้งแรก")


def sync_employee_to_sheet(emp: dict):
    if not GAS_WEB_APP_URL:
        return
    payload = {
        "action": "save_employee", "employee_id": emp["id"], "username": emp["username"],
        "password_hash": emp["password_hash"], "full_name": emp["full_name"],
        "role": emp["role"], "status": emp["status"],
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


# ======================================================
# ================  ข้อมูลเดิมของระบบ  ==================
# ======================================================

def load_data_from_google_sheets():
    global ADMINS_DB, CHAT_SESSIONS_DB
    try:
        resp_admin = requests.get(GAS_WEB_APP_URL, params={"action": "get_admins"})
        if resp_admin.status_code == 200:
            rows = resp_admin.json()
            loaded_admins = []

            for idx, row in enumerate(rows[1:], start=1):
                if not row or all(not str(cell).strip() for cell in row):
                    continue

                try:
                    a_id = int(row[0]) if len(row) > 0 and str(row[0]).strip().isdigit() else idx
                except:
                    a_id = idx

                name = str(row[1]) if len(row) > 1 and str(row[1]).strip() else f"Admin-{a_id}"
                ai_role = str(row[2]) if len(row) > 2 and str(row[2]).strip() in AI_ROLES else "admin"
                channels_raw = str(row[3]) if len(row) > 3 else "[]"
                keywords = str(row[4]) if len(row) > 4 else "ลดราคา, ขอราคาพิเศษ"
                system_prompt = str(row[5]) if len(row) > 5 else "คุณคือแอดมิน AI อัจฉริยะ ตอบกระชับ เป็นมืออาชีพ"
                gender = str(row[6]) if len(row) > 6 and str(row[6]).strip() else "ครับ"

                loaded_admins.append({
                    "id": a_id,
                    "name": name,
                    "ai_role": ai_role,
                    "status": "คล่องแคล่ว",
                    "gender": gender,
                    "channels": [c.strip() for c in channels_raw.replace("[", "").replace("]", "").replace("'", "").split(",") if c.strip()],
                    "keywords": keywords,
                    "system_prompt": system_prompt,
                    "categories": [],
                    "faq_pairs": [],
                    "pending_count": 0
                })

            if loaded_admins:
                ADMINS_DB = loaded_admins
            else:
                ADMINS_DB = []

        resp_chat = requests.get(GAS_WEB_APP_URL, params={"action": "get_chats"})
        if resp_chat.status_code == 200:
            chat_rows = resp_chat.json()
            temp_chats = {}

            for row in chat_rows[1:]:
                if not row or len(row) < 4:
                    continue

                time_cell = str(row[0])
                time_str = time_cell.split("T")[-1][:5] if "T" in time_cell else (time_cell.split(" ")[-1][:5] if " " in time_cell else "00:00")
                c_id = str(row[1]).strip() if len(row) > 1 else "CUST-001"
                c_name = str(row[2]).strip() if len(row) > 2 else "ลูกค้าทั่วไป"
                msg = str(row[3]) if len(row) > 3 else ""
                sender = str(row[4]).strip() if len(row) > 4 else "Client"

                if not c_id:
                    continue

                for admin in ADMINS_DB:
                    admin_id_key = admin["id"]
                    if admin_id_key not in temp_chats:
                        temp_chats[admin_id_key] = {}

                    if c_id not in temp_chats[admin_id_key]:
                        temp_chats[admin_id_key][c_id] = {
                            "customer_id": c_id,
                            "customer_name": c_name,
                            "logs": []
                        }

                    tag = "📥 ข้อความขาเข้า"
                    if "AI" in sender:
                        tag = "🤖 AI วิเคราะห์และตอบอัตโนมัติ"
                    elif "Human" in sender or "ทีมงาน" in sender:
                        tag = "👤 [Human Handover]"

                    temp_chats[admin_id_key][c_id]["logs"].append({
                        "time": time_str,
                        "sender": sender,
                        "text": msg,
                        "tag": tag
                    })

            for a_key, customers in temp_chats.items():
                if a_key not in CHAT_SESSIONS_DB:
                    CHAT_SESSIONS_DB[a_key] = []
                for cust_id, cust_data in customers.items():
                    cust_data["logs"] = list(reversed(cust_data["logs"]))
                CHAT_SESSIONS_DB[a_key] = list(customers.values())

    except Exception as e:
        print(f"Error loading data from Google Sheets: {e}")


load_data_from_google_sheets()
load_employees_from_sheet()


def save_chat_to_google_sheet(customer_id, customer_name, message, sender_type):
    if not GAS_WEB_APP_URL:
        return
    payload = {
        "action": "save_chat",
        "customer_id": customer_id,
        "customer_name": customer_name,
        "message": message,
        "sender_type": sender_type
    }
    try:
        requests.post(GAS_WEB_APP_URL, json=payload)
    except Exception as e:
        print(f"Error syncing chat to Google Sheet: {e}")


def sync_admin_to_sheet(admin_data):
    if not GAS_WEB_APP_URL:
        return
    payload = {
        "action": "save_admin",
        "admin_id": admin_data["id"],
        "name": admin_data["name"],
        "company": admin_data["ai_role"],
        "gender": admin_data.get("gender", "ครับ"),
        "channels": str(admin_data["channels"]),
        "keywords": admin_data["keywords"],
        "system_prompt": admin_data["system_prompt"]
    }
    try:
        requests.post(GAS_WEB_APP_URL, json=payload)
    except Exception as e:
        print(f"Error syncing admin to Google Sheet: {e}")


def delete_admin_from_sheet(admin_id):
    if not GAS_WEB_APP_URL:
        return
    payload = {
        "action": "delete_admin",
        "admin_id": admin_id
    }
    try:
        requests.post(GAS_WEB_APP_URL, json=payload)
    except Exception as e:
        print(f"Error deleting admin from Google Sheet: {e}")


def call_gemini_ai(admin_info: dict, knowledge_context: str, customer_message: str) -> str:
    faq_pairs = admin_info.get("faq_pairs", [])
    for faq in faq_pairs:
        trigger = faq.get("trigger", "").strip()
        answer = faq.get("answer", "").strip()
        image_url = faq.get("image_url", "").strip()

        if trigger and trigger.lower() in customer_message.lower():
            response_result = answer
            if image_url:
                response_result += f"\n[รูปภาพประกอบ: {image_url}]"
            return response_result

    if not GEMINI_API_KEY:
        return f"[จำลอง AI]: ได้รับข้อความ '{customer_message}' แล้ว (กรุณาตั้งค่า GEMINI_API_KEY ใน Environment Variables)"

    try:
        gender_term = admin_info.get('gender', 'ครับ')
        role_title = AI_ROLES.get(admin_info.get('ai_role'), {}).get('name', 'พนักงาน AI')
        profile_instruction = f"""
        [ตำแหน่งและหน้าที่พนักงาน AI]: {role_title}
        - ชื่อตัวแทน/ผู้ดูแล: {admin_info.get('name', 'Admin')}
        - บริษัท/แบรนด์: Kelyfos Facade
        - สรรพนามลงท้าย/เพศการพูดคุย: ลงท้ายด้วย '{gender_term}'

        [คำสั่งพฤติกรรมและบุคลิก (System Prompt)]:
        {admin_info.get('system_prompt', 'คุณคือแอดมิน AI อัจฉริยะ ตอบกระชับ ชัดเจน ตรงประเด็น เป็นมืออาชีพ')}

        [กฎเหล็กการตอบข้อความ]:
        1. ตอบให้กระชับ เป็นมืออาชีพ ห้ามเยิ่นเย้อ อธิบายเฉพาะสิ่งที่ลูกค้าถาม
        2. ใช้สรรพนามลงท้ายว่า '{gender_term}' ทุกครั้งอย่างสม่ำเสมอ

        [เงื่อนไขคีย์เวิร์ดส่งต่อทีมงาน]:
        {admin_info.get('keywords', '')}

        [คลังข้อมูลความรู้และอ้างอิง]:
        {knowledge_context}
        """

        model = genai.GenerativeModel(
            model_name="gemini-3.7-flash",
            system_instruction=profile_instruction
        )
        response = model.generate_content(customer_message)
        return response.text
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการเชื่อมต่อ AI: {str(e)}"


# ======================================================
# ===============  LOGIN / LOGOUT ROUTES  ===============
# ======================================================

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


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return _login_page_html()


@app.post("/login")
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
        secure=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


# ======================================================
# ============  หน้าจัดการพนักงาน (owner เท่านั้น)  =======
# ======================================================

@app.get("/employees", response_class=HTMLResponse)
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


@app.get("/employees/create", response_class=HTMLResponse)
async def employee_create_page(request: Request):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard
    return _employee_form_html(None, "➕ เพิ่มพนักงานใหม่")


@app.get("/employees/edit/{employee_id}", response_class=HTMLResponse)
async def employee_edit_page(request: Request, employee_id: int):
    guard = auth_guard(request, PAGE_PERMISSIONS["employee_management"])
    if not isinstance(guard, dict):
        return guard
    emp = next((e for e in EMPLOYEES_DB if e["id"] == employee_id), None)
    if not emp:
        return RedirectResponse(url="/employees", status_code=303)
    return _employee_form_html(emp, f"⚙️ แก้ไขพนักงาน: {emp['full_name']}")


@app.post("/employees/save")
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


@app.get("/employees/delete/{employee_id}")
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


# ======================================================
# ===============  หน้า Dashboard หลัก (เปลี่ยนหัวข้อเป็นตำแหน่ง)  ==
# ======================================================

@app.get("/", response_class=HTMLResponse)
async def main_dashboard(request: Request):
    guard = auth_guard(request, PAGE_PERMISSIONS["dashboard"])
    if not isinstance(guard, dict):
        return guard
    employee = guard

    load_data_from_google_sheets()

    channels_html = ""
    for ch in CONNECTED_CHANNELS:
        channels_html += f"""
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 12px 16px; margin-bottom: 8px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center;">
            <span style="font-weight: 500; color: #1e293b;">{ch['name']}</span>
            <span style="background: #dcfce7; color: #166534; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600;">● {ch['status']}</span>
        </div>
        """

    rows_html = ""
    if not ADMINS_DB:
        rows_html = "<tr><td colspan='6' style='text-align: center; color: #64748b; padding: 20px;'>ยังไม่มีข้อมูลพนักงาน AI ในระบบ</td></tr>"
    else:
        for admin in ADMINS_DB:
            role_display = AI_ROLES.get(admin.get("ai_role"), {}).get("name", "แอดมินรับแขก")
            badge_bg = "#fee2e2" if admin.get("pending_count", 0) > 0 else "#dcfce7"
            badge_color = "#991b1b" if admin.get("pending_count", 0) > 0 else "#166534"
            badge_text = f"🔴 รอคนตอบ ({admin.get('pending_count', 0)} เคส)" if admin.get("pending_count", 0) > 0 else "● ปกติ"
            channels_str = ", ".join(admin["channels"]) if admin["channels"] else "ยังไม่ได้เชื่อมต่อ"

            rows_html += f"""
            <tr>
                <td style="font-weight: 600; color: #0f172a;">{admin['name']} <span style="font-size:11px; color:#64748b; font-weight:normal;">({admin.get('gender', 'ครับ')})</span></td>
                <td><span style="background: #e0f2fe; color: #0369a1; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600;">{role_display}</span></td>
                <td><span style="color: #059669; font-weight: 500;">{admin['status']}</span></td>
                <td style="color: #475569; font-size: 13px;">{channels_str}</td>
                <td><span style="background: {badge_bg}; color: {badge_color}; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; white-space: nowrap;">{badge_text}</span></td>
                <td style="white-space: nowrap;">
                    <a href="/chat/{admin['id']}" class="btn-action btn-blue">Live Monitor</a>
                    <a href="/edit/{admin['id']}" class="btn-action btn-gray">ตั้งค่า</a>
                    <a href="/delete/{admin['id']}" class="btn-action btn-red" onclick="return confirm('คุณต้องการลบพนักงาน AI นี้ใช่หรือไม่?');">ลบ</a>
                </td>
            </tr>
            """

    employee_menu_html = ""
    if employee["role"] == "owner":
        employee_menu_html = '<a href="/employees" class="btn-action btn-gray" style="margin-right:10px;">👥 จัดการพนักงาน</a>'

    html_content = f"""
    <html>
        <head>
            <title>ศูนย์ควบคุม AI สำหรับผู้ดูแลระบบ</title>
            <style>
                body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f1f5f9; margin: 0; padding: 30px; color: #334155; }}
                .container {{ max-width: 1250px; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); margin: auto; }}
                .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 30px; }}
                .header-right {{ display:flex; align-items:center; }}
                h2 {{ color: #0f172a; margin: 0; font-size: 24px; }}
                .user-info {{ font-size: 13px; color:#64748b; margin-bottom:15px; text-align:right; }}
                .user-info a {{ color:#ef4444; text-decoration:none; font-weight:600; margin-left:10px; }}
                .btn-create {{ background: #10b981; color: white; padding: 10px 20px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px; white-space: nowrap; }}
                .panel {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 20px; border-radius: 8px; margin-bottom: 30px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
                th, td {{ border-bottom: 1px solid #e2e8f0; padding: 14px 12px; text-align: left; vertical-align: middle; }}
                th {{ background-color: #f8fafc; color: #475569; font-size: 13px; font-weight: 600; text-transform: uppercase; }}
                tr:hover {{ background-color: #f8fafc; }}
                .btn-action {{ display: inline-block; padding: 6px 12px; text-decoration: none; border-radius: 6px; font-size: 13px; font-weight: 500; margin-right: 4px; color: white; text-align: center; }}
                .btn-blue {{ background: #2563eb; }}
                .btn-gray {{ background: #64748b; }}
                .btn-red {{ background: #ef4444; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="user-info">
                    👤 {employee['full_name']} ({ROLES.get(employee['role'], employee['role'])})
                    <a href="/logout">ออกจากระบบ</a>
                </div>
                <div class="header">
                    <h2>🤖 ศูนย์ควบคุม AI สำหรับผู้ดูแลระบบ</h2>
                    <div class="header-right">
                        {employee_menu_html}
                        <a href="/create-role" class="btn-create">+ สร้างพนักงาน</a>
                    </div>
                </div>
                <div class="panel">
                    <h3 style="margin-top: 0; color: #1e293b; font-size: 16px; margin-bottom: 15px;">🔗 ช่องทางการเชื่อมต่อ</h3>
                    {channels_html}
                </div>
                <h3>พนักงาน AI ทั้งหมดในระบบ</h3>
                <table>
                    <thead>
                        <tr>
                            <th width="18%">ชื่อพนักงาน</th>
                            <th width="22%">ตำแหน่ง (Role)</th>
                            <th width="10%">สถานะระบบ</th>
                            <th width="22%">ช่องต่างๆ</th>
                            <th width="14%">สถานะคิว</th>
                            <th width="14%">การกระทำ</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/delete/{admin_id}")
async def delete_admin(request: Request, admin_id: int):
    guard = auth_guard(request, PAGE_PERMISSIONS["admin_settings"])
    if not isinstance(guard, dict):
        return guard

    delete_admin_from_sheet(admin_id)
    global ADMINS_DB
    ADMINS_DB = [a for a in ADMINS_DB if a["id"] != admin_id]
    return RedirectResponse(url="/", status_code=303)


# ======================================================
# ===============  หน้าเลือก Role ก่อนสร้างพนักงาน  =====
# ======================================================

@app.get("/create-role", response_class=HTMLResponse)
async def create_role_page(request: Request):
    guard = auth_guard(request, PAGE_PERMISSIONS["admin_settings"])
    if not isinstance(guard, dict):
        return guard

    cards_html = ""
    for r_key, r_info in AI_ROLES.items():
        cards_html += f"""
        <div style="background:#f8fafc; border:1px solid #cbd5e1; padding:25px; border-radius:10px; margin-bottom:15px; display:flex; justify-content:space-between; align-items:center;">
            <div>
                <h3 style="margin:0 0 6px 0; color:#1e293b; font-size:18px;">{r_info['name']}</h3>
                <p style="margin:0; color:#64748b; font-size:14px;">{r_info['desc']}</p>
            </div>
            <a href="/create?role={r_key}" style="background:#2563eb; color:white; padding:10px 20px; text-decoration:none; border-radius:6px; font-weight:600; white-space:nowrap;">เลือกตำแหน่งนี้</a>
        </div>
        """

    return f"""
    <html>
        <head>
            <title>เลือกตำแหน่งพนักงาน AI</title>
            <style>
                body {{ font-family: 'Inter', sans-serif; background: #f1f5f9; padding: 40px; color: #334155; }}
                .box {{ max-width: 750px; margin: auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); }}
                .back-link {{ text-decoration: none; color: #2563eb; font-weight: 600; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="box">
                <a href="/" class="back-link">← กลับหน้าหลัก</a>
                <h2 style="color: #0f172a; margin-top: 20px;">👤 กรุณาเลือกตำแหน่งพนักงาน AI ที่ต้องการสร้าง</h2>
                <p style="color: #64748b; font-size: 14px; margin-bottom: 25px;">เลือกตำแหน่งเพื่อให้ระบบกำหนดขอบเขตหน้าที่และสเปกข้อมูลเฉพาะทางให้ถูกต้อง</p>
                {cards_html}
            </div>
        </body>
    </html>
    """


# ======================================================
# ===============  หน้าฟอร์มสร้าง/ตั้งค่า AI (รองรับเลือก Role)  =
# ======================================================

@app.get("/create", response_class=HTMLResponse)
@app.get("/edit/{admin_id}", response_class=HTMLResponse)
async def edit_admin_page(request: Request, admin_id: Optional[int] = None, role: Optional[str] = "admin"):
    guard = auth_guard(request, PAGE_PERMISSIONS["admin_settings"])
    if not isinstance(guard, dict):
        return guard

    admin_data = {
        "id": "", "name": "", "ai_role": role, "gender": "ครับ", "channels": [],
        "keywords": "ลดราคา, ขอราคาพิเศษ, คุยกับคน, นัดดูหน้างาน",
        "system_prompt": "คุณคือแอดมิน AI อัจฉริยะ ตอบคำถามกระชับ เป็นมืออาชีพ ตรงประเด็น",
        "categories": [{"cat_name": "General Knowledge", "drive_link": "", "files": []}],
        "faq_pairs": [{"trigger": "ขอเรทราคา", "answer": "สวัสดีครับ ส่งเรทราคามาตรฐานให้ครับ", "image_url": "https://example.com/image.jpg"}]
    }

    if admin_id:
        for a in ADMINS_DB:
            if a["id"] == admin_id:
                admin_data = a
                role = a.get("ai_role", "admin")
                break

    role_title = AI_ROLES.get(role, {}).get("name", "พนักงาน AI")
    title = f"⚙️ ตั้งค่าพนักงาน [{role_title}]: {admin_data['name']}" if admin_id else f"➕ สร้างพนักงานใหม่: {role_title}"

    channels_checkboxes = ""
    for ch in CONNECTED_CHANNELS:
        checked = "checked" if ch['name'] in admin_data.get('channels', []) else ""
        channels_checkboxes += f"""
        <label style="font-weight: normal; margin-right: 20px; display: inline-flex; align-items: center; cursor: pointer; color: #334155;">
            <input type="checkbox" name="channels" value="{ch['name']}" {checked} style="margin-right: 8px; width: 16px; height: 16px;"> {ch['name']}
        </label>
        """

    gender_selected_krab = "selected" if admin_data.get("gender") == "ครับ" else ""
    gender_selected_ka = "selected" if admin_data.get("gender") == "ค่ะ" else ""
    gender_selected_neutral = "selected" if admin_data.get("gender") == "สุภาพ" else ""

    categories_html = ""
    for idx, cat in enumerate(admin_data.get('categories', [])):
        files_str = ", ".join(cat.get('files', [])) if cat.get('files') else "ยังไม่มีไฟล์แนบ"
        categories_html += f"""
        <div class="category-item" style="background: #f8fafc; padding: 20px; border: 1px solid #cbd5e1; border-radius: 8px; margin-bottom: 15px;">
            <label style="font-size: 13px; color: #475569;">ชื่อหมวดหมู่</label>
            <input type="text" name="cat_name" value="{cat.get('cat_name', '')}" placeholder="เช่น เรทราคา, สเปกเทคนิค" required>
            <label style="font-size: 13px; color: #475569; margin-top: 12px;">ลิงก์คลาวด์จัดเก็บ (Google Drive / Sheets)</label>
            <input type="text" name="cat_link" value="{cat.get('drive_link', '')}" placeholder="https://docs.google.com/...">
            <label style="font-size: 13px; color: #475569; margin-top: 12px;">อัปโหลดเอกสาร (ปัจจุบัน: {files_str})</label>
            <input type="file" name="cat_file" style="margin-top: 5px;">
            <button type="button" onclick="this.parentElement.remove()" style="background: #ef4444; color: white; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; margin-top: 15px; font-weight: 500; font-size: 13px;">ลบหมวดหมู่นี้</button>
        </div>
        """

    faq_html = ""
    for faq in admin_data.get('faq_pairs', []):
        faq_html += f"""
        <div class="faq-item" style="background: #f8fafc; padding: 15px; border: 1px solid #cbd5e1; border-radius: 8px; margin-bottom: 12px;">
            <div style="display: flex; gap: 10px; margin-bottom: 8px;">
                <input type="text" name="faq_trigger" value="{faq.get('trigger', '')}" placeholder="คำที่ลูกค้าพิมพ์ เช่น ขอราคา" style="flex: 1; margin-top:0;">
                <input type="text" name="faq_answer" value="{faq.get('answer', '')}" placeholder="คำตอบข้อความสำเร็จรูป" style="flex: 2; margin-top:0;">
            </div>
            <div style="display: flex; gap: 10px; align-items: center;">
                <input type="text" name="faq_image" value="{faq.get('image_url', '')}" placeholder="🖼️ URL ลิงก์รูปภาพประกอบ (ไม่ใส่ก็ได้ เช่น https://.../pic.jpg)" style="flex: 1; margin-top:0; font-size: 13px;">
                <button type="button" onclick="this.parentElement.parentElement.remove()" style="background: #ef4444; color: white; border: none; padding: 10px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap;">ลบแถวนี้</button>
            </div>
        </div>
        """

    return f"""
    <html>
        <head>
            <title>{title}</title>
            <style>
                body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f1f5f9; padding: 30px; color: #334155; }}
                .form-box {{ max-width: 900px; margin: auto; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); }}
                label {{ font-weight: 600; margin-top: 20px; display: block; color: #1e293b; font-size: 14px; }}
                input[type=text], select, textarea {{ width: 100%; padding: 12px; margin-top: 6px; border: 1px solid #cbd5e1; border-radius: 8px; box-sizing: border-box; font-size: 14px; }}
                textarea {{ height: 100px; }}
                .section {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 25px; border-radius: 8px; margin-top: 25px; }}
                .btn-save {{ background: #2563eb; color: white; padding: 12px 25px; border: none; border-radius: 8px; font-size: 15px; cursor: pointer; margin-top: 25px; font-weight: 600; }}
                .btn-add {{ background: #0ea5e9; color: white; border: none; padding: 10px 18px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 14px; margin-top: 10px; }}
                .btn-back {{ text-decoration: none; color: #64748b; margin-left: 15px; font-weight: 500; font-size: 14px; }}
            </style>
            <script>
                function addCategoryField() {{
                    const container = document.getElementById('categories-container');
                    const div = document.createElement('div');
                    div.className = 'category-item';
                    div.style = "background: #f8fafc; padding: 20px; border: 1px solid #cbd5e1; border-radius: 8px; margin-bottom: 15px;";
                    div.innerHTML = `
                        <label style="font-size: 13px; color: #475569;">ชื่อหมวดหมู่</label>
                        <input type="text" name="cat_name" placeholder="เช่น สเปกวัสดุ" required>
                        <label style="font-size: 13px; color: #475569; margin-top: 12px;">ลิงก์คลาวด์จัดเก็บ (Google Drive / Sheets)</label>
                        <input type="text" name="cat_link" placeholder="https://docs.google.com/...">
                        <label style="font-size: 13px; color: #475569; margin-top: 12px;">อัปโหลดเอกสาร</label>
                        <input type="file" name="cat_file" style="margin-top: 5px;">
                        <button type="button" onclick="this.parentElement.remove()" style="background: #ef4444; color: white; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; margin-top: 15px; font-size: 13px;">ลบหมวดหมู่นี้</button>
                    `;
                    container.appendChild(div);
                }}

                function addFaqField() {{
                    const container = document.getElementById('faq-container');
                    const div = document.createElement('div');
                    div.className = 'faq-item';
                    div.style = "background: #f8fafc; padding: 15px; border: 1px solid #cbd5e1; border-radius: 8px; margin-bottom: 12px;";
                    div.innerHTML = `
                        <div style="display: flex; gap: 10px; margin-bottom: 8px;">
                            <input type="text" name="faq_trigger" placeholder="คำที่ลูกค้าพิมพ์ เช่น ขอราคา" style="flex: 1; margin-top:0;">
                            <input type="text" name="faq_answer" placeholder="คำตอบข้อความสำเร็จรูป" style="flex: 2; margin-top:0;">
                        </div>
                        <div style="display: flex; gap: 10px; align-items: center;">
                            <input type="text" name="faq_image" placeholder="🖼️ URL ลิงก์รูปภาพประกอบ (ไม่ใส่ก็ได้ เช่น https://.../pic.jpg)" style="flex: 1; margin-top:0; font-size: 13px;">
                            <button type="button" onclick="this.parentElement.parentElement.remove()" style="background: #ef4444; color: white; border: none; padding: 10px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap;">ลบแถวนี้</button>
                        </div>
                    `;
                    container.appendChild(div);
                }}
            </script>
        </head>
        <body>
            <div class="form-box">
                <h2 style="color: #0f172a; margin-top: 0; font-size: 24px;">{title}</h2>
                <form action="/save-admin" method="POST" enctype="multipart/form-data">
                    <input type="hidden" name="admin_id" value="{admin_data['id']}">
                    <input type="hidden" name="ai_role" value="{role}">
                    
                    <label>ชื่อพนักงาน AI</label>
                    <input type="text" name="name" value="{admin_data['name']}" required placeholder="เช่น AI ฝ่ายขาย 01">
                    
                    <label>เพศ / สรรพนามลงท้ายของ AI (Gender / Tone)</label>
                    <select name="gender">
                        <option value="ครับ" {gender_selected_krab}>ครับ (ชาย / ทางการ)</option>
                        <option value="ค่ะ" {gender_selected_ka}>ค่ะ (หญิง / เป็นกันเอง)</option>
                        <option value="สุภาพ" {gender_selected_neutral}>สุภาพ / กลางๆ</option>
                    </select>

                    <label>ช่องทางการเชื่อมต่อที่เลือก</label>
                    <div style="margin-top: 8px; padding: 15px; background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px;">
                        {channels_checkboxes}
                    </div>
                    <div class="section">
                        <h3 style="margin-top: 0; font-size: 16px;">🤖 บุคลิกและคำสั่งพฤติกรรม AI (System Prompt)</h3>
                        <textarea name="system_prompt">{admin_data['system_prompt']}</textarea>
                    </div>
                    
                    <div class="section">
                        <h3 style="margin-top: 0; font-size: 16px;">⚡ คลังคู่คำถาม-คำตอบยอดฮิต (Dynamic FAQ Pairs รองรับส่งรูปภาพ)</h3>
                        <p style="font-size: 13px; color: #64748b; margin-top: 0;">ตั้งค่าคำถามพร้อมข้อความและลิงก์รูปภาพเพื่อส่งให้ลูกค้าอัตโนมัติ</p>
                        <div id="faq-container" style="margin-top: 15px;">
                            {faq_html}
                        </div>
                        <button type="button" class="btn-add" onclick="addFaqField()">+ เพิ่มคู่ FAQ พร้อมรูปภาพ</button>
                    </div>

                    <div class="section">
                        <h3 style="margin-top: 0; font-size: 16px;">🚨 เงื่อนไขการส่งต่อให้ทีมงาน</h3>
                        <input type="text" name="keywords" value="{admin_data['keywords']}">
                    </div>
                    
                    <div class="section">
                        <h3 style="margin-top: 0; font-size: 16px;">📁 คลังข้อมูลอ้างอิงและคลังสินทรัพย์</h3>
                        <div id="categories-container" style="margin-top: 15px;">
                            {categories_html}
                        </div>
                        <button type="button" class="btn-add" onclick="addCategoryField()">+ เพิ่มหมวดหมู่</button>
                    </div>
                    <button type="submit" class="btn-save">บันทึกการตั้งค่า</button>
                    <a href="/create-role" class="btn-back">ยกเลิก</a>
                </form>
            </div>
        </body>
    </html>
    """


@app.post("/save-admin")
async def save_admin(
    request: Request,
    admin_id: str = Form(""),
    name: str = Form(...),
    ai_role: str = Form("admin"),
    gender: str = Form("ครับ"),
    system_prompt: str = Form(""),
    keywords: str = Form("")
):
    guard = auth_guard(request, PAGE_PERMISSIONS["admin_settings"])
    if not isinstance(guard, dict):
        return guard

    form_data = await request.form()
    selected_channels = form_data.getlist("channels")
    cat_names = form_data.getlist("cat_name")
    cat_links = form_data.getlist("cat_link")
    cat_files = form_data.getlist("cat_file")
    faq_triggers = form_data.getlist("faq_trigger")
    faq_answers = form_data.getlist("faq_answer")
    faq_images = form_data.getlist("faq_image")

    categories_list = []
    for i, c_name in enumerate(cat_names):
        if not c_name.strip():
            continue
        c_link = cat_links[i] if i < len(cat_links) else ""
        files_collected = []
        if i < len(cat_files):
            uploaded_file = cat_files[i]
            if uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
                content = await uploaded_file.read()
                save_path = os.path.join(UPLOAD_DIR, uploaded_file.filename)
                with open(save_path, "wb") as f:
                    f.write(content)
                files_collected.append(uploaded_file.filename)
        categories_list.append({"cat_name": c_name, "drive_link": c_link, "files": files_collected})

    faq_list = []
    for i, trig in enumerate(faq_triggers):
        if trig.strip():
            ans = faq_answers[i] if i < len(faq_answers) else ""
            img = faq_images[i] if i < len(faq_images) else ""
            faq_list.append({"trigger": trig.strip(), "answer": ans.strip(), "image_url": img.strip()})

    saved_admin_obj = None
    if admin_id and admin_id.isdigit():
        for a in ADMINS_DB:
            if a["id"] == int(admin_id):
                a["name"] = name
                a["ai_role"] = ai_role
                a["gender"] = gender
                a["channels"] = selected_channels
                a["system_prompt"] = system_prompt
                a["keywords"] = keywords
                a["categories"] = categories_list
                a["faq_pairs"] = faq_list
                saved_admin_obj = a
                break
    else:
        max_id = max([a["id"] for a in ADMINS_DB]) if ADMINS_DB else 0
        new_id = max_id + 1
        saved_admin_obj = {
            "id": new_id, "name": name, "ai_role": ai_role, "status": "คล่องแคล่ว",
            "gender": gender, "channels": selected_channels, "keywords": keywords, 
            "system_prompt": system_prompt, "categories": categories_list, 
            "faq_pairs": faq_list, "pending_count": 0
        }
        ADMINS_DB.append(saved_admin_obj)
        CHAT_SESSIONS_DB[new_id] = []

    if saved_admin_obj:
        sync_admin_to_sheet(saved_admin_obj)

    return RedirectResponse(url="/", status_code=303)


# ======================================================
# 3. API ส่งข้อความและบันทึกลง Google Sheets ทันที
# ======================================================
@app.post("/chat/{admin_id}/send")
async def send_chat_message(
    request: Request,
    admin_id: int,
    customer_id: str = Form(...),
    sender_type: str = Form(...),
    message_text: str = Form(...)
):
    guard = auth_guard(request, PAGE_PERMISSIONS["chat_monitor"])
    if not isinstance(guard, dict):
        return guard

    sessions = CHAT_SESSIONS_DB.get(admin_id, [])
    admin_info = next((a for a in ADMINS_DB if a["id"] == admin_id), None)
    
    if not admin_info:
        admin_info = {
            "id": admin_id, "name": "AI Admin", "ai_role": "admin", 
            "gender": "ครับ", "channels": [], "keywords": "", 
            "system_prompt": "คุณคือแอดมิน AI", "categories": [], "faq_pairs": []
        }

    knowledge_context = ""
    if "categories" in admin_info:
        knowledge_context = "\n".join([f"- หมวด {cat['cat_name']}: ลิงก์อ้างอิง {cat['drive_link']}" for cat in admin_info["categories"]])

    keywords_list = [k.strip() for k in admin_info["keywords"].split(",")] if admin_info else []

    current_customer_name = "Unknown"
    for s in sessions:
        if s["customer_id"] == customer_id:
            current_customer_name = s["customer_name"]
            break
            
    if sender_type == "Client":
        save_chat_to_google_sheet(customer_id, current_customer_name, message_text, "Client (ลูกค้า)")
        
        needs_handover = any(kw in message_text for kw in keywords_list if kw)
        if needs_handover:
            ai_response_text = "ตรวจพบเงื่อนไขสำคัญตามคำสั่งระบบ ทำการยกธงแดงส่งต่อให้ทีมงานมืออาชีพดูแลต่อครับ"
            ai_tag = "⚠️ Need Human Intervention"
            for admin in ADMINS_DB:
                if admin["id"] == admin_id:
                    admin["pending_count"] = admin.get("pending_count", 0) + 1
        else:
            ai_response_text = call_gemini_ai(admin_info, knowledge_context, message_text)
            ai_tag = "🤖 AI วิเคราะห์และตอบอัตโนมัติ"

        save_chat_to_google_sheet(customer_id, current_customer_name, ai_response_text, "AI Agent")
        
    elif sender_type == "Human Agent":
        save_chat_to_google_sheet(customer_id, current_customer_name, message_text, "Human Agent (ทีมงาน)")
        for admin in ADMINS_DB:
            if admin["id"] == admin_id and admin.get("pending_count", 0) > 0:
                admin["pending_count"] -= 1
            
    return RedirectResponse(url=f"/chat/{admin_id}?customer={customer_id}", status_code=303)


# ======================================================
# 4. Facebook Messenger Webhook Handlers
# ======================================================
@app.get("/webhook")
async def verify_facebook_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    if mode == "subscribe" and token == FB_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_facebook_webhook(request: Request):
    body = await request.json()
    
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender_id = messaging.get("sender", {}).get("id")
                message_text = messaging.get("message", {}).get("text")
                
                if sender_id and message_text:
                    target_admin_id = ADMINS_DB[0]["id"] if ADMINS_DB else 1
                    admin_info = next((a for a in ADMINS_DB if a["id"] == target_admin_id), None)
                    
                    if not admin_info:
                        admin_info = {
                            "id": 1, "name": "AI Admin", "ai_role": "admin", 
                            "gender": "ครับ", "channels": [], "keywords": "", 
                            "system_prompt": "คุณคือแอดมิน AI", "categories": [], "faq_pairs": []
                        }

                    knowledge_context = ""
                    if "categories" in admin_info:
                        knowledge_context = "\n".join([f"- หมวด {cat['cat_name']}: {cat['drive_link']}" for cat in admin_info["categories"]])

                    save_chat_to_google_sheet(sender_id, f"FB-User-{sender_id[-4:]}", message_text, "Client (ลูกค้า)")
                    ai_response_text = call_gemini_ai(admin_info, knowledge_context, message_text)
                    save_chat_to_google_sheet(sender_id, f"FB-User-{sender_id[-4:]}", ai_response_text, "AI Agent")

                    if FB_PAGE_ACCESS_TOKEN and FB_PAGE_ACCESS_TOKEN != "ใส่_Page_Access_Token_ของ Facebook_ที่นี่":
                        url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
                        payload = {
                            "recipient": {"id": sender_id},
                            "message": {"text": ai_response_text}
                        }
                        requests.post(url, json=payload)

    return {"status": "EVENT_RECEIVED"}


# ======================================================
# 5. Live Chat Monitor UI
# ======================================================
@app.get("/chat/{admin_id}", response_class=HTMLResponse)
async def chat_monitor(request: Request, admin_id: int, customer: Optional[str] = None):
    guard = auth_guard(request, PAGE_PERMISSIONS["chat_monitor"])
    if not isinstance(guard, dict):
        return guard

    load_data_from_google_sheets()
    
    admin_info = next((a for a in ADMINS_DB if a["id"] == admin_id), None)
    admin_name = admin_info["name"] if admin_info else f"Agent ID {admin_id}"
    
    sessions = CHAT_SESSIONS_DB.get(admin_id, [])
    customer_buttons = ""
    selected_logs = []
    current_customer_name = ""
    
    if sessions:
        if not customer and sessions:
            customer = sessions[0]["customer_id"]
            
        for s in sessions:
            is_active = "background: #2563eb; color: white;" if s["customer_id"] == customer else "background: #f8fafc; color: #334155; border: 1px solid #e2e8f0;"
            customer_buttons += f"""
            <a href="/chat/{admin_id}?customer={s['customer_id']}" style="display: block; padding: 12px 15px; margin-bottom: 8px; border-radius: 8px; text-decoration: none; {is_active} font-weight: 500; font-size: 14px;">
                👤 {s['customer_name']} ({s['customer_id']})
            </a>
            """
            if s["customer_id"] == customer:
                selected_logs = s["logs"]
                current_customer_name = s["customer_name"]
    else:
        customer_buttons = "<p style='color: #64748b; font-size: 13px;'>ยังไม่มีประวัติลูกค้าใน Google Sheets</p>"

    logs_html = ""
    if not selected_logs:
        logs_html = "<tr><td colspan='5' style='text-align:center; color: #64748b; padding: 30px;'>เลือกรายชื่อลูกค้าด้านซ้ายเพื่อดูประวัติการสนทนา</td></tr>"
    else:
        for log in selected_logs:
            recipient = "AI แอดมิน" if "Client" in log['sender'] or "ลูกค้า" in log['sender'] else current_customer_name

            tag_style = "color: #059669; font-weight: 500;"
            if "Need Human" in log['tag'] or "⚠️" in log['tag']:
                tag_style = "color: #dc2626; font-weight: 600;"
            elif "Human" in log['tag'] or "ทีมงาน" in log['tag']:
                tag_style = "color: #2563eb; font-weight: 600;"
            elif "ข้อความขาเข้า" in log['tag'] or "Client" in log['tag']:
                tag_style = "color: #d97706; font-weight: 600;"

            logs_html += f"""
            <tr>
                <td style="color: #64748b; font-size: 13px;">{log['time']}</td>
                <td style="font-weight: 600; color: #1e293b;">{log['sender']}</td>
                <td style="color: #475569; font-weight: 500;">{recipient}</td>
                <td style="color: #334155;">{log['text']}</td>
                <td><span style="{tag_style}">{log['tag']}</span></td>
            </tr>
            """

    chat_form_html = ""
    if customer:
        chat_form_html = f"""
        <div style="margin-top: 20px; background: #f8fafc; border: 1px solid #cbd5e1; padding: 20px; border-radius: 8px;">
            <h4 style="margin-top: 0; color: #1e293b; font-size: 15px; margin-bottom: 12px;">✍️ จำลองการส่งข้อความโต้ตอบ ({current_customer_name})</h4>
            <form action="/chat/{admin_id}/send" method="POST">
                <input type="hidden" name="customer_id" value="{customer}">
                
                <div style="display: flex; gap: 15px; margin-bottom: 12px;">
                    <label style="font-weight: 500; cursor: pointer;">
                        <input type="radio" name="sender_type" value="Client" checked> ส่งในนาม <b>ลูกค้า (Client)</b> [ให้ Gemini AI วิเคราะห์และตอบ]
                    </label>
                    <label style="font-weight: 500; cursor: pointer;">
                        <input type="radio" name="sender_type" value="Human Agent"> ส่งในนาม <b>ทีมงาน (Human Agent)</b>
                    </label>
                </div>

                <div style="display: flex; gap: 10px;">
                    <input type="text" name="message_text" placeholder="พิมพ์ข้อความที่ลูกค้าส่งมา..." required style="flex-grow: 1; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px;">
                    <button type="submit" style="background: #10b981; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; white-space: nowrap;">ส่งข้อความ</button>
                </div>
            </form>
        </div>
        """

    return f"""
    <html>
        <head>
            <title>ตรวจสอบการสนทนา - {admin_name}</title>
            <style>
                body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f1f5f9; padding: 30px; color: #334155; }}
                .container {{ max-width: 1250px; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); margin: auto; }}
                .back-link {{ text-decoration: none; color: #2563eb; font-weight: 600; font-size: 14px; display: inline-block; margin-bottom: 20px; }}
                .layout {{ display: flex; gap: 25px; }}
                .sidebar {{ width: 320px; background: #f8fafc; border: 1px solid #e2e8f0; padding: 20px; border-radius: 8px; height: fit-content; }}
                .content-area {{ flex-grow: 1; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                th, td {{ border-bottom: 1px solid #e2e8f0; padding: 12px; text-align: left; }}
                th {{ background-color: #f8fafc; color: #475569; font-size: 13px; font-weight: 600; text-transform: uppercase; }}
                tr:hover {{ background-color: #f8fafc; }}
            </style>
        </head>
        <body>
            <div class="container">
                <a href="/" class="back-link">← กลับสู่หน้าหลัก</a>
                <h2 style="color: #0f172a; margin-top: 0; font-size: 24px;">💬 ตรวจสอบการสนทนา: {admin_name}</h2>
                
                <div class="layout">
                    <div class="sidebar">
                        <h3 style="margin-top: 0; font-size: 16px; color: #1e293b; margin-bottom: 15px;">👥 รายชื่อลูกค้าในระบบ</h3>
                        {customer_buttons}
                    </div>

                    <div class="content-area">
                        <h3 style="margin-top: 0; font-size: 16px; color: #1e293b; margin-bottom: 15px;">📜 บันทึกข้อความและการสนทนา</h3>
                        <table>
                            <thead>
                                <tr>
                                    <th width="12%">เวลา</th>
                                    <th width="18%">ผู้ส่ง</th>
                                    <th width="18%">ผู้รับ</th>
                                    <th width="34%">ข้อความ / คำสั่ง</th>
                                    <th width="18%">สถานะ / ป้ายกำกับ</th>
                                </tr>
                            </thead>
                            <tbody>
                                {logs_html}
                            </tbody>
                        </table>

                        {chat_form_html}
                    </div>
                </div>
            </div>
        </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

```
