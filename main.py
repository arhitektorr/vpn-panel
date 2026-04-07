import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import subprocess
import tempfile
import re
import json
from datetime import datetime

app = FastAPI()

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
ENDPOINT = os.getenv("ENDPOINT", "")
DNS = os.getenv("DNS", "1.1.1.1, 8.8.8.8")




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


class ClientCreate(BaseModel):
    phone: str
    prefix: str
    expires_at: str


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


def get_server_public_key() -> str:
    return run_cmd(f"docker exec {CONTAINER_NAME} cat {SERVER_PUBLIC_KEY_PATH}")


def build_client_config(client: sqlite3.Row) -> str:
    with open(TEMPLATE_PATH, "r", encoding="utf-8", newline="\n") as f:
        config = f.read()

    config = re.sub(
        r"(?m)^Address = .*$",
        f"Address = {client['assigned_ip']}/32",
        config
    )
    config = re.sub(
        r"(?m)^PrivateKey = .*$",
        f"PrivateKey = {client['private_key']}",
        config
    )
    config = re.sub(
        r"(?m)^PresharedKey = .*$",
        f"PresharedKey = {client['preshared_key']}",
        config
    )

    return config


@app.get("/")
def root():
    return {"status": "VPN panel API working"}


@app.get("/clients")
def list_clients():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients ORDER BY id DESC")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


@app.post("/clients")
def create_client(data: ClientCreate):
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
        """, (
            data.phone,
            client_name,
            public_key,
            private_key,
            preshared_key,
            ip,
            data.expires_at,
            1,
            created_at
        ))
        conn.commit()
        client_id = cur.lastrowid
        conn.close()

        return {
            "id": client_id,
            "phone": data.phone,
            "client_name": client_name,
            "public_key": public_key,
            "private_key": private_key,
            "preshared_key": preshared_key,
            "assigned_ip": ip,
            "expires_at": data.expires_at,
            "is_enabled": True
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clients/{client_id}/disable")
def disable_client(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Клиент не найден")

    if row["is_enabled"] == 0:
        conn.close()
        return {"message": "Клиент уже отключён"}

    try:
        remove_peer_from_wireguard(row["public_key"])
        cur.execute("UPDATE clients SET is_enabled = 0 WHERE id = ?", (client_id,))
        conn.commit()
        conn.close()
        return {"message": "Клиент отключён"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

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
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@app.post("/clients/{client_id}/enable")
def enable_client(client_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Клиент не найден")

    if row["is_enabled"] == 1:
        conn.close()
        return {"message": "Клиент уже включён"}

    try:
        add_peer_to_wireguard(
            row["public_key"],
            row["preshared_key"],
            row["assigned_ip"]
        )
        cur.execute("UPDATE clients SET is_enabled = 1 WHERE id = ?", (client_id,))
        conn.commit()
        conn.close()
        return {"message": "Клиент включён"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

def save_runtime_config():
    config = run_cmd(
        f'docker exec {CONTAINER_NAME} sh -c "wg showconf {WG_INTERFACE}"'
    )
    with open("/opt/vpn-panel/wg0.runtime.conf", "w") as f:
        f.write(config + "\n")
    run_cmd(f"docker cp /opt/vpn-panel/wg0.runtime.conf {CONTAINER_NAME}:/opt/amnezia/awg/wg0.conf")


def add_client_to_table(public_key: str, name: str):
    # 1. скачать текущий файл
    run_cmd(f"docker cp {CONTAINER_NAME}:{CLIENTS_TABLE_PATH} {PANEL_TMP_DIR}/clientsTable.json")

    # 2. прочитать
    with open(f"{PANEL_TMP_DIR}/clientsTable.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # 3. добавить клиента
    data.append({
        "clientId": public_key,
        "userData": {
            "clientName": name,
            "creationDate": datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        }
    })

    # 4. сохранить
    with open(f"{PANEL_TMP_DIR}/clientsTable.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    # 5. вернуть в контейнер
    run_cmd(f"docker cp {PANEL_TMP_DIR}/clientsTable.json {CONTAINER_NAME}:{CLIENTS_TABLE_PATH}")
