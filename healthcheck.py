#!/usr/bin/env python3
"""
VPN ARHITEKTOR PANEL - Healthcheck скрипт
Для мониторинга состояния панели
"""

import sqlite3
import subprocess
import sys
import os

DB_PATH = "/opt/vpn-panel/vpn.db"
CONTAINER_NAME = "amnezia-awg2"

def check_database():
    """Проверка базы данных"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM clients")
        count = cur.fetchone()[0]
        conn.close()
        print(f"✅ База данных OK (клиентов: {count})")
        return True
    except Exception as e:
        print(f"❌ База данных: {e}")
        return False

def check_container():
    """Проверка контейнера AmneziaWG"""
    try:
        result = subprocess.run(
            f"docker ps --filter name={CONTAINER_NAME} --format '{{{{.Status}}}}'",
            shell=True, capture_output=True, text=True
        )
        if result.stdout.strip():
            print(f"✅ Контейнер {CONTAINER_NAME} запущен: {result.stdout.strip()}")
            return True
        else:
            print(f"❌ Контейнер {CONTAINER_NAME} не запущен")
            return False
    except Exception as e:
        print(f"❌ Ошибка проверки контейнера: {e}")
        return False

def check_wireguard():
    """Проверка WireGuard интерфейса"""
    try:
        result = subprocess.run(
            f"docker exec {CONTAINER_NAME} wg show",
            shell=True, capture_output=True, text=True
        )
        if "interface:" in result.stdout:
            print("✅ WireGuard интерфейс активен")
            return True
        else:
            print("❌ WireGuard интерфейс не активен")
            return False
    except Exception as e:
        print(f"❌ Ошибка проверки WireGuard: {e}")
        return False

def main():
    print()
    print("=" * 40)
    print("VPN ARHITEKTOR PANEL - HEALTHCHECK")
    print("=" * 40)
    print()
    
    checks = []
    
    # Проверка БД
    checks.append(check_database())
    
    # Проверка контейнера
    checks.append(check_container())
    
    # Проверка WireGuard
    checks.append(check_wireguard())
    
    print()
    print("=" * 40)
    
    if all(checks):
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
        print("=" * 40)
        sys.exit(0)
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ")
        print("=" * 40)
        sys.exit(1)

if __name__ == "__main__":
    main()
