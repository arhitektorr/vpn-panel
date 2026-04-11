#!/usr/bin/env python3
"""
VPN ARHITEKTOR PANEL - Перевыпуск ключей для всех клиентов
Используется при переносе на новый сервер
"""

import sqlite3
import subprocess
import tempfile
import os
import sys
from datetime import datetime

DB_PATH = "/opt/vpn-panel/vpn.db"
CONTAINER_NAME = "amnezia-awg2"
WG_INTERFACE = "awg0"

def run_cmd(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()

def generate_private_key():
    return run_cmd(f"docker exec {CONTAINER_NAME} wg genkey")

def generate_public_key(private_key):
    cmd = f"docker exec -i {CONTAINER_NAME} sh -c 'wg pubkey'"
    result = subprocess.run(cmd, shell=True, input=private_key, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(result.stderr.strip())
    return result.stdout.strip()

def generate_psk():
    return run_cmd(f"docker exec {CONTAINER_NAME} wg genpsk")

def remove_peer_from_wireguard(public_key):
    try:
        run_cmd(f'docker exec {CONTAINER_NAME} sh -c "wg set {WG_INTERFACE} peer {public_key} remove"')
    except:
        pass

def add_peer_to_wireguard(public_key, psk, ip):
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(psk)
        temp_path = f.name
    try:
        run_cmd(f"docker cp {temp_path} {CONTAINER_NAME}:/tmp/psk")
        run_cmd(f'docker exec {CONTAINER_NAME} sh -c "wg set {WG_INTERFACE} peer {public_key} preshared-key /tmp/psk allowed-ips {ip}/32"')
    finally:
        os.remove(temp_path)

def main():
    print()
    print("=" * 50)
    print("VPN ARHITEKTOR PANEL - ПЕРЕВЫПУСК КЛЮЧЕЙ")
    print("=" * 50)
    print()
    print("⚠️  ВНИМАНИЕ!")
    print("Эта операция перевыпустит ключи для ВСЕХ клиентов.")
    print("После этого старые конфиги клиентов перестанут работать.")
    print()
    
    confirm = input("Вы уверены? (да/нет): ")
    if confirm.lower() != "да":
        print("Отменено.")
        return
    
    print()
    print("Подключение к базе данных...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients")
    clients = cur.fetchall()
    
    if not clients:
        print("Клиенты не найдены.")
        conn.close()
        return
    
    print(f"Найдено клиентов: {len(clients)}")
    print()
    
    for client in clients:
        print(f"🔄 Обработка: {client['client_name']} (ID: {client['id']})")
        
        try:
            # Генерируем новые ключи
            private_key = generate_private_key()
            public_key = generate_public_key(private_key)
            preshared_key = generate_psk()
            
            # Обновляем в базе данных
            cur.execute("""
                UPDATE clients 
                SET private_key = ?, public_key = ?, preshared_key = ?
                WHERE id = ?
            """, (private_key, public_key, preshared_key, client['id']))
            
            # Удаляем старый пир из WireGuard
            remove_peer_from_wireguard(client['public_key'])
            
            # Добавляем новый пир в WireGuard
            add_peer_to_wireguard(public_key, preshared_key, client['assigned_ip'])
            
            print(f"   ✅ Ключи обновлены")
            
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
    
    conn.commit()
    conn.close()
    
    print()
    print("=" * 50)
    print("✅ ГОТОВО!")
    print("=" * 50)
    print()
    print("Теперь нужно разослать клиентам НОВЫЕ конфиги.")
    print("Старые конфиги больше не работают.")
    print()

if __name__ == "__main__":
    main()
