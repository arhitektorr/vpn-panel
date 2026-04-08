import threading
import time
import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Response, Request, Form, Depends, Query, Cookie
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime, timedelta
import sqlite3
import subprocess
import tempfile
import re
import json
import secrets
import qrcode
import uuid
from io import BytesIO
from collections import defaultdict
from typing import Optional

# ========== ПРОВЕРКА НАЛИЧИЯ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
API_TOKEN = os.getenv("API_TOKEN")

if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise ValueError(
        "\n" + "="*60 + "\n"
        "❌ ОШИБКА КОНФИГУРАЦИИ\n"
        "="*60 + "\n"
        "Не заданы переменные окружения:\n"
        f"  ADMIN_USERNAME: {'✅' if ADMIN_USERNAME else '❌ не задан'}\n"
        f"  ADMIN_PASSWORD: {'✅' if ADMIN_PASSWORD else '❌ не задана'}\n\n"
        "Создайте файл /opt/vpn-panel/.env с содержимым:\n"
        "  ADMIN_USERNAME=admin\n"
        "  ADMIN_PASSWORD=ваш_сложный_пароль_здесь\n"
        "  API_TOKEN=ваш_секретный_токен_для_api\n\n"
        "После создания файла перезапустите панель.\n"
        "="*60
    )

if not API_TOKEN:
    API_TOKEN = secrets.token_urlsafe(32)
    print(f"\n⚠️ ВНИМАНИЕ: API_TOKEN не задан в .env файле!")
    print(f"Сгенерирован автоматически: {API_TOKEN}")
    print(f"Добавьте строку в /opt/vpn-panel/.env:\nAPI_TOKEN={API_TOKEN}\n")

# ========== НАСТРОЙКИ FASTAPI ==========
app = FastAPI(
    title="VPN ARHITEKTOR API",
    description="API для управления VPN клиентами",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

templates = Jinja2Templates(directory="/opt/vpn-panel/templates")
active_sessions = defaultdict(set)

# ========== АВТОРИЗАЦИЯ ==========
def verify_web_admin(request: Request):
    session_token = request.cookies.get("session_token")
    client_ip = request.client.host
    
    if session_token and session_token in active_sessions.get(client_ip, set()):
        return True
    
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            import base64
            encoded = auth_header[6:]
            decoded = base64.b64decode(encoded).decode()
            username, password = decoded.split(":", 1)
            if secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(password, ADMIN_PASSWORD):
                return True
        except:
            pass
    
    return False

security = HTTPBearer(auto_error=False)

def verify_api_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Отсутствует токен авторизации",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not secrets.compare_digest(credentials.credentials, API_TOKEN):
        raise HTTPException(
            status_code=403,
            detail="Недействительный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return True

# ========== НАСТРОЙКИ ==========
DB_PATH = os.getenv("DB_PATH", "/opt/vpn-panel/vpn.db")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "amnezia-awg2")
WG_INTERFACE = os.getenv("WG_INTERFACE", "awg0")
SERVER_PUBLIC_KEY_PATH = os.getenv(
    "SERVER_PUBLIC_KEY_PATH",
    "/opt/amnezia/awg/wireguard_server_public_key.key"
)
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "/opt/vpn-panel/awg_template.conf")
CLIENTS_TABLE_PATH = os.getenv("CLIENTS_TABLE_PATH", "/opt/amnezia/awg/clientsTable")
PANEL_TMP_DIR = os.getenv("PANEL_TMP_DIR", "/opt/vpn-panel")

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        client_name TEXT NOT NULL,
        public_key TEXT NOT NULL UNIQUE,
        private_key TEXT NOT NULL,
        preshared_key TEXT NOT NULL,
        assigned_ip TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ========== МОДЕЛИ ==========
class ClientCreate(BaseModel):
    phone: str
    prefix: str
    expires_at: str

class ClientUpdate(BaseModel):
    phone: Optional[str] = None
    expires_at: Optional[str] = None
    is_enabled: Optional[bool] = None

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def run_cmd(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()

def generate_private_key() -> str:
    return run_cmd(f"docker exec {CONTAINER_NAME} wg genkey")

def generate_public_key(private_key: str) -> str:
    cmd = f"docker exec -i {CONTAINER_NAME} sh -c 'wg pubkey'"
    result = subprocess.run(
        cmd,
        shell=True,
        input=private_key,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise Exception(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()

def generate_psk() -> str:
    return run_cmd(f"docker exec {CONTAINER_NAME} wg genpsk")

def get_next_ip() -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT assigned_ip FROM clients")
    rows = cur.fetchall()
    conn.close()

    used = set()
    for row in rows:
        ip = row[0]
        last = int(ip.split(".")[-1])
        used.add(last)

    for i in range(2, 255):
        if i not in used:
            return f"10.8.1.{i}"

    raise Exception("Нет свободных IP")

def add_peer_to_wireguard(public_key: str, psk: str, ip: str):
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(psk)
        temp_path = f.name

    try:
        run_cmd(f"docker cp {temp_path} {CONTAINER_NAME}:/tmp/psk")
        run_cmd(
            f'docker exec {CONTAINER_NAME} sh -c '
            f'"wg set {WG_INTERFACE} peer {public_key} preshared-key /tmp/psk allowed-ips {ip}/32"'
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def remove_peer_from_wireguard(public_key: str):
    run_cmd(
        f'docker exec {CONTAINER_NAME} sh -c '
        f'"wg set {WG_INTERFACE} peer {public_key} remove"'
    )

def build_client_config(client) -> str:
    with open(TEMPLATE_PATH, "r", encoding="utf-8", newline="\n") as f:
        config = f.read()

    config = re.sub(r"(?m)^Address = .*$", f"Address = {client['assigned_ip']}/32", config)
    config = re.sub(r"(?m)^PrivateKey = .*$", f"PrivateKey = {client['private_key']}", config)
    config = re.sub(r"(?m)^PresharedKey = .*$", f"PresharedKey = {client['preshared_key']}", config)

    return config

def add_client_to_table(public_key: str, name: str):
    try:
        run_cmd(f"docker cp {CONTAINER_NAME}:{CLIENTS_TABLE_PATH} {PANEL_TMP_DIR}/clientsTable.json")
    except:
        with open(f"{PANEL_TMP_DIR}/clientsTable.json", "w") as f:
            json.dump([], f)

    with open(f"{PANEL_TMP_DIR}/clientsTable.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    data.append({
        "clientId": public_key,
        "userData": {
            "clientName": name,
            "creationDate": datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        }
    })

    with open(f"{PANEL_TMP_DIR}/clientsTable.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    run_cmd(f"docker cp {PANEL_TMP_DIR}/clientsTable.json {CONTAINER_NAME}:{CLIENTS_TABLE_PATH}")

def remove_client_from_table(public_key: str):
    try:
        run_cmd(f"docker cp {CONTAINER_NAME}:{CLIENTS_TABLE_PATH} {PANEL_TMP_DIR}/clientsTable.json")
    except:
        return

    with open(f"{PANEL_TMP_DIR}/clientsTable.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    data = [item for item in data if item.get("clientId") != public_key]

    with open(f"{PANEL_TMP_DIR}/clientsTable.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    run_cmd(f"docker cp {PANEL_TMP_DIR}/clientsTable.json {CONTAINER_NAME}:{CLIENTS_TABLE_PATH}")

def disable_expired_clients():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE is_enabled = 1")
    rows = cur.fetchall()
    now = datetime.now()

    for row in rows:
        expires_at = row["expires_at"]
        if not expires_at:
            continue

        try:
            try:
                expiry_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M")
            except:
                expiry_dt = datetime.strptime(expires_at, "%Y-%m-%d")
        except:
            continue

        if expiry_dt <= now:
            try:
                remove_peer_from_wireguard(row["public_key"])
            except:
                pass
            cur.execute("UPDATE clients SET is_enabled = 0 WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()

def background_worker():
    while True:
        try:
            disable_expired_clients()
        except Exception as e:
            print("Background error:", e)
        time.sleep(30)

threading.Thread(target=background_worker, daemon=True).start()

# ========== ОТКРЫТЫЕ ЭНДПОИНТЫ ==========
@app.get("/")
def root():
    return {"status": "VPN panel API working", "docs": "/docs"}

@app.get("/client/{client_id}", response_class=HTMLResponse)
def client_page(request: Request, client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return HTMLResponse("Клиент не найден", status_code=404)

    client = dict(row)
    
    created_at = client.get("created_at")
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            client["created_at_formatted"] = dt.strftime("%d.%m.%Y %H:%M")
        except:
            client["created_at_formatted"] = created_at
    else:
        client["created_at_formatted"] = ""

    expires_at = client.get("expires_at")
    client["is_expired"] = False
    if expires_at:
        try:
            try:
                dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            except:
                try:
                    dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M")
                except:
                    dt = datetime.strptime(expires_at, "%Y-%m-%d")
            client["expires_at_formatted"] = dt.strftime("%d.%m.%Y %H:%M")
            if dt <= datetime.now():
                client["is_expired"] = True
        except:
            client["expires_at_formatted"] = expires_at
    else:
        client["expires_at_formatted"] = ""

    return templates.TemplateResponse(request, "client_page.html", {"client": client})

@app.get("/clients/{client_id}/download")
def download_client_config(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    config = build_client_config(row)
    filename = f"{row['client_name']}.conf"

    return Response(
        content=config,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/clients/{client_id}/qrcode")
def get_client_qrcode(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    config = build_client_config(row)
    
    qr = qrcode.QRCode(version=5, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
    qr.add_data(config)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#1d1d1f", back_color="#ffffff")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    
    return StreamingResponse(buf, media_type="image/png")

# ========== ЛОГИН/ВЫХОД ==========
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = None):
    if verify_web_admin(request):
        return RedirectResponse(url="/panel", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(password, ADMIN_PASSWORD):
        session_token = str(uuid.uuid4())
        client_ip = request.client.host
        active_sessions[client_ip].add(session_token)
        
        response = RedirectResponse(url="/panel", status_code=303)
        response.set_cookie(key="session_token", value=session_token, httponly=True, max_age=86400, samesite="lax", path="/")
        return response
    
    return RedirectResponse(url="/login?error=Неверный+логин+или+пароль", status_code=303)

@app.get("/logout")
def logout(request: Request):
    session_token = request.cookies.get("session_token")
    client_ip = request.client.host
    
    if session_token and client_ip in active_sessions:
        active_sessions[client_ip].discard(session_token)
        if not active_sessions[client_ip]:
            del active_sessions[client_ip]
    
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response

# ========== ВЕБ-ЭНДПОИНТЫ ДЛЯ ПАНЕЛИ ==========
@app.post("/web/clients")
async def web_create_client(request: Request, phone: str = Form(...), prefix: str = Form(...), expires_at: str = Form(...)):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    try:
        private_key = generate_private_key()
        public_key = generate_public_key(private_key)
        preshared_key = generate_psk()
        ip = get_next_ip()

        client_name = f"{prefix}_{phone}_{public_key[:6]}"
        created_at = datetime.utcnow().isoformat()

        add_peer_to_wireguard(public_key, preshared_key, ip)
        add_client_to_table(public_key, client_name)
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO clients
            (phone, client_name, public_key, private_key, preshared_key, assigned_ip, expires_at, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (phone, client_name, public_key, private_key, preshared_key, ip, expires_at, 1, created_at))
        conn.commit()
        conn.close()

        return RedirectResponse(url="/panel", status_code=303)

    except Exception as e:
        print(f"Error creating client: {e}")
        return RedirectResponse(url="/panel?error=Ошибка+при+создании", status_code=303)

@app.get("/panel", response_class=HTMLResponse)
async def panel(request: Request, page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100)):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    disable_expired_clients()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as total FROM clients")
    total_count = cur.fetchone()["total"]
    
    offset = (page - 1) * per_page
    cur.execute("SELECT * FROM clients ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, offset))
    rows = cur.fetchall()
    conn.close()

    clients = []

    for row in rows:
        client = dict(row)

        created_at = client.get("created_at")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                client["created_at_formatted"] = dt.strftime("%d.%m.%Y %H:%M")
            except:
                client["created_at_formatted"] = created_at
        else:
            client["created_at_formatted"] = ""

        expires_at = client.get("expires_at")
        client["is_expired"] = False

        if expires_at:
            try:
                try:
                    dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M")
                except:
                    dt = datetime.strptime(expires_at, "%Y-%m-%d")
                client["expires_at_formatted"] = dt.strftime("%d.%m.%Y %H:%M")
                if dt <= datetime.now():
                    client["is_expired"] = True
            except:
                client["expires_at_formatted"] = expires_at
        else:
            client["expires_at_formatted"] = ""

        clients.append(client)

    total_pages = (total_count + per_page - 1) // per_page
    start_page = max(1, page - 2)
    end_page = min(total_pages, page + 2)
    
    if end_page - start_page < 4:
        if start_page == 1:
            end_page = min(total_pages, start_page + 4)
        elif end_page == total_pages:
            start_page = max(1, end_page - 4)
    
    page_range = range(start_page, end_page + 1)

    return templates.TemplateResponse(request, "clients.html", {
        "clients": clients, "page": page, "per_page": per_page,
        "total_count": total_count, "total_pages": total_pages,
        "page_range": page_range, "start_page": start_page, "end_page": end_page
    })

@app.post("/panel/clients/{client_id}/disable")
async def panel_disable_client(request: Request, client_id: int):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if row:
        try:
            remove_peer_from_wireguard(row["public_key"])
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE clients SET is_enabled = 0 WHERE id = ?", (client_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error: {e}")
    
    return RedirectResponse(url="/panel", status_code=303)

@app.post("/panel/clients/{client_id}/enable")
async def panel_enable_client(request: Request, client_id: int):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if row:
        try:
            add_peer_to_wireguard(row["public_key"], row["preshared_key"], row["assigned_ip"])
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE clients SET is_enabled = 1 WHERE id = ?", (client_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error: {e}")
    
    return RedirectResponse(url="/panel", status_code=303)

@app.post("/panel/clients/{client_id}/delete")
async def panel_delete_client(request: Request, client_id: int):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()

    if row:
        try:
            remove_peer_from_wireguard(row["public_key"])
        except:
            pass
        try:
            remove_client_from_table(row["public_key"])
        except:
            pass
        cur.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()

    conn.close()
    return RedirectResponse(url="/panel", status_code=303)

@app.post("/panel/clients/{client_id}/extend")
async def panel_extend_client(request: Request, client_id: int, days: int = Form(...)):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT expires_at FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()

    if row:
        current_expiry = row["expires_at"]
        try:
            if current_expiry:
                expiry_date = datetime.strptime(current_expiry, "%Y-%m-%d")
            else:
                expiry_date = datetime.now()
        except:
            expiry_date = datetime.now()

        new_expiry = expiry_date + timedelta(days=days)
        cur.execute("UPDATE clients SET expires_at = ? WHERE id = ?", (new_expiry.strftime("%Y-%m-%d"), client_id))
        conn.commit()

    conn.close()
    return RedirectResponse(url="/panel", status_code=303)

@app.get("/panel/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_page(request: Request, client_id: int):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return RedirectResponse(url="/panel", status_code=303)

    return templates.TemplateResponse(request, "edit_client.html", {"request": request, "client": dict(row)})

@app.post("/panel/clients/{client_id}/edit")
async def edit_client_save(request: Request, client_id: int, phone: str = Form(...), expires_at: str = Form(...)):
    if not verify_web_admin(request):
        return RedirectResponse(url="/login", status_code=303)
    
    expires_at = expires_at.replace("T", " ")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE clients SET phone = ?, expires_at = ? WHERE id = ?", (phone, expires_at, client_id))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/panel", status_code=303)

# ========== API ЭНДПОИНТЫ (ТРЕБУЮТ TOKEN) ==========
@app.get("/api/clients", dependencies=[Depends(verify_api_token)])
def api_list_clients():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients ORDER BY id DESC")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"success": True, "data": rows}

@app.get("/api/clients/{client_id}", dependencies=[Depends(verify_api_token)])
def api_get_client(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return {"success": True, "data": dict(row)}

@app.post("/api/clients", dependencies=[Depends(verify_api_token)])
def api_create_client(data: ClientCreate):
    try:
        private_key = generate_private_key()
        public_key = generate_public_key(private_key)
        preshared_key = generate_psk()
        ip = get_next_ip()

        client_name = f"{data.prefix}_{data.phone}_{public_key[:6]}"
        created_at = datetime.utcnow().isoformat()

        add_peer_to_wireguard(public_key, preshared_key, ip)
        add_client_to_table(public_key, client_name)
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO clients
            (phone, client_name, public_key, private_key, preshared_key, assigned_ip, expires_at, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data.phone, client_name, public_key, private_key, preshared_key, ip, data.expires_at, 1, created_at))
        conn.commit()
        client_id = cur.lastrowid
        conn.close()

        return {
            "success": True,
            "message": "Клиент создан",
            "data": {
                "id": client_id, "phone": data.phone, "client_name": client_name,
                "public_key": public_key, "private_key": private_key, "preshared_key": preshared_key,
                "assigned_ip": ip, "expires_at": data.expires_at, "is_enabled": True
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/clients/{client_id}", dependencies=[Depends(verify_api_token)])
def api_update_client(client_id: int, data: ClientUpdate):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    existing = cur.fetchone()
    
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    updates, params = [], []
    
    if data.phone is not None:
        updates.append("phone = ?")
        params.append(data.phone)
    if data.expires_at is not None:
        updates.append("expires_at = ?")
        params.append(data.expires_at)
    if data.is_enabled is not None:
        updates.append("is_enabled = ?")
        params.append(1 if data.is_enabled else 0)
        if data.is_enabled and existing["is_enabled"] == 0:
            add_peer_to_wireguard(existing["public_key"], existing["preshared_key"], existing["assigned_ip"])
        elif not data.is_enabled and existing["is_enabled"] == 1:
            remove_peer_from_wireguard(existing["public_key"])
    
    if updates:
        params.append(client_id)
        cur.execute(f"UPDATE clients SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    updated = cur.fetchone()
    conn.close()
    
    return {"success": True, "message": "Клиент обновлён", "data": dict(updated)}

@app.delete("/api/clients/{client_id}", dependencies=[Depends(verify_api_token)])
def api_delete_client(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()

    if row:
        try:
            remove_peer_from_wireguard(row["public_key"])
        except:
            pass
        try:
            remove_client_from_table(row["public_key"])
        except:
            pass
        cur.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()

    conn.close()
    return {"success": True, "message": "Клиент удалён"}

@app.post("/api/clients/{client_id}/disable", dependencies=[Depends(verify_api_token)])
def api_disable_client(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    try:
        remove_peer_from_wireguard(row["public_key"])
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE clients SET is_enabled = 0 WHERE id = ?", (client_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Клиент отключён"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/enable", dependencies=[Depends(verify_api_token)])
def api_enable_client(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    try:
        add_peer_to_wireguard(row["public_key"], row["preshared_key"], row["assigned_ip"])
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE clients SET is_enabled = 1 WHERE id = ?", (client_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Клиент включён"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/extend", dependencies=[Depends(verify_api_token)])
def api_extend_client(client_id: int, days: int = Query(..., ge=1, le=365)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT expires_at FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Клиент не найден")

    try:
        if row["expires_at"]:
            expiry_date = datetime.strptime(row["expires_at"], "%Y-%m-%d")
        else:
            expiry_date = datetime.now()
    except:
        expiry_date = datetime.now()

    new_expiry = expiry_date + timedelta(days=days)
    cur.execute("UPDATE clients SET expires_at = ? WHERE id = ?", (new_expiry.strftime("%Y-%m-%d"), client_id))
    conn.commit()
    conn.close()

    return {"success": True, "message": f"Подписка продлена на {days} дней", "new_expires_at": new_expiry.strftime("%Y-%m-%d")}

@app.get("/api/stats", dependencies=[Depends(verify_api_token)])
def api_stats():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM clients")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) as active FROM clients WHERE is_enabled = 1")
    active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) as expired FROM clients WHERE expires_at < date('now') AND is_enabled = 1")
    expired = cur.fetchone()[0]
    conn.close()
    
    return {"success": True, "data": {"total": total, "active": active, "expired": expired}}

if __name__ == "__main__":
    import uvicorn
    print(f"\n🚀 VPN ARHITEKTOR API запущен")
    print(f"📖 Документация API: http://localhost:8000/docs")
    print(f"🔐 API Token: {API_TOKEN}")
    print(f"🌐 Веб-панель: http://localhost:8000/panel\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
