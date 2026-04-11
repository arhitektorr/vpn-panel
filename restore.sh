#!/bin/bash

# ============================================
# VPN ARHITEKTOR PANEL - Скрипт восстановления
# ============================================

if [ -z "$1" ]; then
    echo ""
    echo "❌ ОШИБКА: Укажите путь к файлу бэкапа"
    echo ""
    echo "Использование: $0 /путь/к/backup_YYYYMMDD_HHMMSS.tar.gz"
    echo ""
    echo "Пример: $0 /opt/vpn-panel/backups/backup_20250409_120000.tar.gz"
    echo ""
    exit 1
fi

BACKUP_FILE=$1
TEMP_DIR="/tmp/vpn_restore_$(date +%s)"

echo ""
echo "========================================="
echo "VPN ARHITEKTOR PANEL - ВОССТАНОВЛЕНИЕ"
echo "========================================="
echo "Файл бэкапа: $BACKUP_FILE"
echo ""

# Проверяем, существует ли файл
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Файл не найден: $BACKUP_FILE"
    exit 1
fi

# 1. Распаковываем бэкап
echo "[1/6] Распаковка бэкапа..."
mkdir -p $TEMP_DIR
tar -xzf $BACKUP_FILE -C $TEMP_DIR
echo "      ✅ Распаковано"

# 2. Останавливаем панель
echo "[2/6] Остановка панели..."
sudo systemctl stop vpn-panel 2>/dev/null
pkill -f "python.*main.py" 2>/dev/null
echo "      ✅ Панель остановлена"

# 3. Восстанавливаем базу данных
echo "[3/6] Восстановление базы данных..."
if [ -f $TEMP_DIR/vpn.db.* ]; then
    cp $(ls $TEMP_DIR/vpn.db.*) /opt/vpn-panel/vpn.db
    echo "      ✅ vpn.db восстановлен"
else
    echo "      ⚠️ vpn.db не найден в бэкапе"
fi

# 4. Восстанавливаем .env
echo "[4/6] Восстановление .env..."
if [ -f $TEMP_DIR/env.* ]; then
    cp $(ls $TEMP_DIR/env.*) /opt/vpn-panel/.env
    echo "      ✅ .env восстановлен"
else
    echo "      ⚠️ .env не найден в бэкапе"
fi

# 5. Восстанавливаем clientsTable.json
echo "[5/6] Восстановление clientsTable.json..."
if [ -f $TEMP_DIR/clientsTable.json.* ]; then
    cp $(ls $TEMP_DIR/clientsTable.json.*) /opt/vpn-panel/clientsTable.json
    echo "      ✅ clientsTable.json восстановлен"
fi

# 6. Восстанавливаем ключи в контейнер
echo "[6/6] Восстановление ключей в контейнер..."
if docker ps | grep -q amnezia-awg2; then
    if [ -f $TEMP_DIR/private_key.* ]; then
        docker cp $(ls $TEMP_DIR/private_key.*) amnezia-awg2:/opt/amnezia/awg/wireguard_server_private_key.key
        echo "      ✅ Приватный ключ восстановлен"
    fi
    if [ -f $TEMP_DIR/public_key.* ]; then
        docker cp $(ls $TEMP_DIR/public_key.*) amnezia-awg2:/opt/amnezia/awg/wireguard_server_public_key.key
        echo "      ✅ Публичный ключ восстановлен"
    fi
else
    echo "      ⚠️ Контейнер amnezia-awg2 не запущен"
fi

# Очистка
rm -rf $TEMP_DIR

echo ""
echo "========================================="
echo "✅ ВОССТАНОВЛЕНИЕ ЗАВЕРШЕНО!"
echo "========================================="
echo ""
echo "⚠️ ВАЖНО:"
echo "1. Проверьте ENDPOINT в .env (возможно, IP изменился)"
echo "2. Запустите панель: sudo systemctl start vpn-panel"
echo "3. Проверьте работу: curl -I https://awg.95dev.ru/panel"
echo ""
echo "Если IP сервера изменился, обновите ENDPOINT в .env"
echo "========================================="
