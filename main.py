from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
import uvicorn
import os
import datetime
from typing import Optional
import google.generativeai as genai
import requests

app = FastAPI()

UPLOAD_DIR = "./uploaded_knowledge"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ----------------------------------------------------
# 🔑 ตั้งค่า Gemini API Key และ Google Apps Script URL
# ----------------------------------------------------
GEMINI_API_KEY = "AQ.Ab8RN6JcdJ7PZEflwyRC4KqYIiE4UbNXBuiHvZ_HDOx11vKUMQ"
if GEMINI_API_KEY and GEMINI_API_KEY != "ใส่_API_KEY_ของคุณที่นี่":
    genai.configure(api_key=GEMINI_API_KEY)

GAS_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzINU5Sgd9OwT5XI2VpgP04YZCDBr2jPXT1k9VzWpdrXq5i_LILDOW_JohOIqVW6b_t/exec"

# ค่า Token สำหรับตั้งค่า Webhook บน Facebook Developers
FB_VERIFY_TOKEN = "kelyfos_verify_token_secure"
FB_PAGE_ACCESS_TOKEN = "ใส่_Page_Access_Token_ของ Facebook_ที่นี่"

CONNECTED_CHANNELS = [
    {"id": "line_01", "name": "LINE OA: @KelyfosFacade", "status": "เชื่อมต่อแล้ว"},
    {"id": "fb_01", "name": "Facebook Messenger: Kelyfos Official", "status": "เชื่อมต่อแล้ว"},
    {"id": "line_02", "name": "LINE OA: @ModernSignage", "status": "เชื่อมต่อแล้ว"}
]

ADMINS_DB = []
CHAT_SESSIONS_DB = {}


# ----------------------------------------------------
# 📥 ฟังก์ชันเชื่อมต่อ Google Sheets (ดึงและบันทึก)
# ----------------------------------------------------
def load_data_from_google_sheets():
    """ดึงข้อมูล Admin และประวัติแชทจาก Google Sheets มาใส่ใน RAM"""
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
                company = str(row[2]) if len(row) > 2 and str(row[2]).strip() else "Kelyfos facade"
                channels_raw = str(row[3]) if len(row) > 3 else "[]"
                keywords = str(row[4]) if len(row) > 4 else "ลดราคา, ขอราคาพิเศษ"
                system_prompt = str(row[5]) if len(row) > 5 else "คุณคือแอดมิน AI อัจฉริยะ ตอบกระชับ เป็นมืออาชีพ"
                gender = str(row[6]) if len(row) > 6 and str(row[6]).strip() else "ครับ"

                loaded_admins.append({
                    "id": a_id,
                    "name": name,
                    "company": company,
                    "status": "คล่องแคล่ว",
                    "gender": gender,
                    "channels": [c.strip() for c in channels_raw.replace("[","").replace("]","").replace("'","").split(",") if c.strip()],
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
                # สลับลำดับให้ข้อความล่าสุดขึ้นก่อนในระดับเซสชันแชท
                for cust_id, cust_data in customers.items():
                    cust_data["logs"] = list(reversed(cust_data["logs"]))
                CHAT_SESSIONS_DB[a_key] = list(customers.values())

    except Exception as e:
        print(f"Error loading data from Google Sheets: {e}")

load_data_from_google_sheets()


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
        "company": admin_data["company"],
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


# ----------------------------------------------------
# 🧠 ฟังก์ชันเรียกใช้งาน Gemini AI (ปรับปรุงโทนเสียงกระชับ + FAQ Matching + Gender)
# ----------------------------------------------------
def call_gemini_ai(admin_info: dict, knowledge_context: str, customer_message: str) -> str:
    # ตรวจสอบ Dynamic FAQ Pairs ก่อนส่งให้ Gemini หากมีคำถามตรงกับคลังสคริปต์
    faq_pairs = admin_info.get("faq_pairs", [])
    for faq in faq_pairs:
        trigger = faq.get("trigger", "").strip()
        answer = faq.get("answer", "").strip()
        if trigger and trigger.lower() in customer_message.lower():
            return answer

    if not GEMINI_API_KEY or GEMINI_API_KEY == "ใส่_API_KEY_克的ที่นี่":
        return f"[จำลอง AI]: ได้รับข้อความ '{customer_message}' แล้ว (กรุณาตรวจสอบ Gemini API Key)"
    
    try:
        gender_term = admin_info.get('gender', 'ครับ')
        profile_instruction = f"""
        [ข้อมูลตัวตนและแบรนด์]:
        - ชื่อตัวแทน/ผู้ดูแล: {admin_info.get('name', 'Admin')}
        - บริษัท/แบรนด์: {admin_info.get('company', 'Kelyfos Facade')}
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


# ----------------------------------------------------
# 1. Main Dashboard
# ----------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def main_dashboard(request: Request):
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
        rows_html = "<tr><td colspan='6' style='text-align: center; color: #64748b; padding: 20px;'>ยังไม่มีข้อมูล Admin ใน Google Sheets</td></tr>"
    else:
        for admin in ADMINS_DB:
            badge_bg = "#fee2e2" if admin.get("pending_count", 0) > 0 else "#dcfce7"
            badge_color = "#991b1b" if admin.get("pending_count", 0) > 0 else "#166534"
            badge_text = f"🔴 รอคนตอบ ({admin.get('pending_count', 0)} เคส)" if admin.get("pending_count", 0) > 0 else "● ปกติ"
            channels_str = ", ".join(admin["channels"]) if admin["channels"] else "ยังไม่ได้เชื่อมต่อ"
            
            rows_html += f"""
            <tr>
                <td style="font-weight: 600; color: #0f172a;">{admin['name']} <span style="font-size:11px; color:#64748b; font-weight:normal;">({admin.get('gender', 'ครับ')})</span></td>
                <td style="color: #475569;">{admin['company']}</td>
                <td><span style="color: #059669; font-weight: 500;">{admin['status']}</span></td>
                <td style="color: #475569; font-size: 13px;">{channels_str}</td>
                <td><span style="background: {badge_bg}; color: {badge_color}; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; white-space: nowrap;">{badge_text}</span></td>
                <td style="white-space: nowrap;">
                    <a href="/chat/{admin['id']}" class="btn-action btn-blue">Live Monitor</a>
                    <a href="/edit/{admin['id']}" class="btn-action btn-gray">ตั้งค่า</a>
                    <a href="/delete/{admin['id']}" class="btn-action btn-red" onclick="return confirm('คุณต้องการลบ Admin นี้ใช่หรือไม่?');">ลบ</a>
                </td>
            </tr>
            """

    html_content = f"""
    <html>
        <head>
            <title>ศูนย์ควบคุม AI สำหรับผู้ดูแลระบบ</title>
            <style>
                body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f1f5f9; margin: 0; padding: 30px; color: #334155; }}
                .container {{ max-width: 1250px; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); margin: auto; }}
                .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 30px; }}
                h2 {{ color: #0f172a; margin: 0; font-size: 24px; }}
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
                <div class="header">
                    <h2>🤖 ศูนย์ควบคุม AI สำหรับผู้ดูแลระบบ (Enterprise AI Management)</h2>
                    <a href="/create" class="btn-create">+ สร้าง AI สำหรับผู้ดูแลระบบ</a>
                </div>
                <div class="panel">
                    <h3 style="margin-top: 0; color: #1e293b; font-size: 16px; margin-bottom: 15px;">🔗 ช่องทางการเชื่อมต่อ</h3>
                    {channels_html}
                </div>
                <h3>ตัวแทน AI ที่ทำงานอยู่และคิวการส่งต่อ (เชื่อมต่อ Google Sheets)</h3>
                <table>
                    <thead>
                        <tr>
                            <th width="18%">ชื่อตัวแทน</th>
                            <th width="18%">บริษัท / แบรนด์</th>
                            <th width="10%">สถานะระบบ</th>
                            <th width="26%">ช่องต่างๆ</th>
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
async def delete_admin(admin_id: int):
    delete_admin_from_sheet(admin_id)
    global ADMINS_DB
    ADMINS_DB = [a for a in ADMINS_DB if a["id"] != admin_id]
    return RedirectResponse(url="/", status_code=303)


# ----------------------------------------------------
# 2. Config Form & Save API (รองรับเพศ & FAQ Pairs)
# ----------------------------------------------------
@app.get("/create", response_class=HTMLResponse)
@app.get("/edit/{admin_id}", response_class=HTMLResponse)
async def edit_admin_page(admin_id: Optional[int] = None):
    admin_data = {
        "id": "", "name": "", "company": "", "gender": "ครับ", "channels": [], 
        "keywords": "ลดราคา, ขอราคาพิเศษ, คุยกับคน, นัดดูหน้างาน", 
        "system_prompt": "คุณคือแอดมิน AI อัจฉริยะ ตอบคำถามกระชับ เป็นมืออาชีพ ตรงประเด็น", 
        "categories": [{"cat_name": "General Knowledge", "drive_link": "", "files": []}],
        "faq_pairs": [{"trigger": "ขอเรทราคา", "answer": "สวัสดีครับ ส่งเรทราคามาตรฐานให้ทางลิงก์นี้ครับ..."}]
    }
    
    if admin_id:
        for a in ADMINS_DB:
            if a["id"] == admin_id:
                admin_data = a
                break

    title = f"⚙️ ตั้งค่าระบบ: {admin_data['name']}" if admin_id else "➕ สร้าง AI สำหรับผู้ดูแลระบบ"

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
        <div class="faq-item" style="background: #f8fafc; padding: 15px; border: 1px solid #cbd5e1; border-radius: 8px; margin-bottom: 12px; display: flex; gap: 10px; align-items: center;">
            <input type="text" name="faq_trigger" value="{faq.get('trigger', '')}" placeholder="คำที่ลูกค้าพิมพ์ เช่น ขอราคา" style="flex: 1; margin-top:0;">
            <input type="text" name="faq_answer" value="{faq.get('answer', '')}" placeholder="คำตอบสำเร็จรูป" style="flex: 2; margin-top:0;">
            <button type="button" onclick="this.parentElement.remove()" style="background: #ef4444; color: white; border: none; padding: 10px 14px; border-radius: 6px; cursor: pointer; font-size: 13px;">ลบ</button>
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
                    div.style = "background: #f8fafc; padding: 15px; border: 1px solid #cbd5e1; border-radius: 8px; margin-bottom: 12px; display: flex; gap: 10px; align-items: center;";
                    div.innerHTML = `
                        <input type="text" name="faq_trigger" placeholder="คำที่ลูกค้าพิมพ์ เช่น ขอราคา" style="flex: 1; margin-top:0;">
                        <input type="text" name="faq_answer" placeholder="คำตอบสำเร็จรูป" style="flex: 2; margin-top:0;">
                        <button type="button" onclick="this.parentElement.remove()" style="background: #ef4444; color: white; border: none; padding: 10px 14px; border-radius: 6px; cursor: pointer; font-size: 13px;">ลบ</button>
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
                    <label>รหัสโปรเจกต์ / ชื่อตัวแทน</label>
                    <input type="text" name="name" value="{admin_data['name']}" required placeholder="เช่น Kelyfos-Admin-01">
                    <label>ชื่อบริษัท / แบรนด์</label>
                    <input type="text" name="company" value="{admin_data['company']}" required placeholder="เช่น ฟาชาดเคลีฟอส">
                    
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
                        <h3 style="margin-top: 0; font-size: 16px;">⚡ คลังคู่คำถาม-คำตอบยอดฮิต (Dynamic FAQ Pairs)</h3>
                        <p style="font-size: 13px; color: #64748b; margin-top: 0;">ตั้งค่าชุดคำถามที่พบบ่อยเพื่อให้ AI ตอบตามสคริปต์นี้ทันทีโดยไม่ต้องผ่านโมเดล</p>
                        <div id="faq-container" style="margin-top: 15px;">
                            {faq_html}
                        </div>
                        <button type="button" class="btn-add" onclick="addFaqField()">+ เพิ่มคู่ FAQ</button>
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
                    <a href="/" class="btn-back">ยกเลิก</a>
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
    company: str = Form(...),
    gender: str = Form("ครับ"),
    system_prompt: str = Form(""),
    keywords: str = Form("")
):
    form_data = await request.form()
    selected_channels = form_data.getlist("channels")
    cat_names = form_data.getlist("cat_name")
    cat_links = form_data.getlist("cat_link")
    cat_files = form_data.getlist("cat_file")
    faq_triggers = form_data.getlist("faq_trigger")
    faq_answers = form_data.getlist("faq_answer")

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
            faq_list.append({"trigger": trig.strip(), "answer": ans.strip()})

    saved_admin_obj = None
    if admin_id and admin_id.isdigit():
        for a in ADMINS_DB:
            if a["id"] == int(admin_id):
                a["name"] = name
                a["company"] = company
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
            "id": new_id, "name": name, "company": company, "status": "คล่องแคล่ว",
            "gender": gender, "channels": selected_channels, "keywords": keywords, 
            "system_prompt": system_prompt, "categories": categories_list, 
            "faq_pairs": faq_list, "pending_count": 0
        }
        ADMINS_DB.append(saved_admin_obj)
        CHAT_SESSIONS_DB[new_id] = []

    if saved_admin_obj:
        sync_admin_to_sheet(saved_admin_obj)

    return RedirectResponse(url="/", status_code=303)


# ----------------------------------------------------
# 3. API ส่งข้อความและบันทึกลง Google Sheets ทันที
# ----------------------------------------------------
@app.post("/chat/{admin_id}/send")
async def send_chat_message(
    admin_id: int,
    customer_id: str = Form(...),
    sender_type: str = Form(...),
    message_text: str = Form(...)
):
    sessions = CHAT_SESSIONS_DB.get(admin_id, [])
    admin_info = next((a for a in ADMINS_DB if a["id"] == admin_id), None)
    
    if not admin_info:
        admin_info = {
            "id": admin_id, "name": "AI Admin", "company": "Kelyfos", 
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


# ----------------------------------------------------
# 4. Facebook Messenger Webhook Handlers
# ----------------------------------------------------
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
                            "id": 1, "name": "AI Admin", "company": "Kelyfos", 
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


# ----------------------------------------------------
# 5. Live Chat Monitor UI (Reverse Chat Order: ล่าสุดขึ้นบนสุด)
# ----------------------------------------------------
@app.get("/chat/{admin_id}", response_class=HTMLResponse)
async def chat_monitor(admin_id: int, customer: Optional[str] = None):
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
                selected_logs = s["logs"] # logs ถูกเรียงใหม่ให้ข้อความล่าสุดอยู่บนสุดแล้วจาก load_data
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
                <h2 style="color: #0f172a; margin-top: 0; font-size: 24px;">💬 ตรวจสอบการสนทนา (ล่าสุดขึ้นบนสุด): {admin_name}</h2>
                
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
