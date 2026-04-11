#!/bin/bash

# ============================================
# VPN ARHITEKTOR PANEL - Скрипт бэкапа
# ============================================

BACKUP_DIR="/opt/vpn-panel/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

echo ""
echo "========================================="
echo "VPN ARHITEKTOR PANEL - БЭКАП"
echo "Дата: $DATE"
echo "========================================="
echo ""

# 1. Бэкап базы данных
echo "[1/5] Бэкап базы данных..."
if [ -f /opt/vpn-panel/vpn.db ]; then
    cp /opt/vpn-panel/vpn.db $BACKUP_DIR/vpn.db.$DATE
    echo "      ✅ vpn.db скопирован"
else
    echo "      ⚠️ vpn.db не найден"
fi

# 2. Бэкап .env
echo "[2/5] Бэкап .env..."
if [ -f /opt/vpn-panel/.env ]; then
    cp /opt/vpn-panel/.env $BACKUP_DIR/env.$DATE
    echo "      ✅ .env скопирован"
else
    echo "      ⚠️ .env не найден"
fi

# 3. Бэкап clientsTable.json
echo "[3/5] Бэкап clientsTable.json..."
if [ -f /opt/vpn-panel/clientsTable.json ]; then
    cp /opt/vpn-panel/clientsTable.json $BACKUP_DIR/clientsTable.json.$DATE
    echo "      ✅ clientsTable.json скопирован"
else
    echo "      ⚠️ clientsTable.json не найден"
fi

# 4. Экспорт ключей из контейнера
echo "[4/5] Экспорт ключей из контейнера..."
if docker exec amnezia-awg2 cat /opt/amnezia/awg/wireguard_server_private_key.key > $BACKUP_DIR/private_key.$DATE 2>/dev/null; then
    echo "      ✅ Приватный ключ экспортирован"
else
    echo "      ⚠️ Не удалось экспортировать ключи (контейнер не запущен?)"
fi

if docker exec amnezia-awg2 cat /opt/amnezia/awg/wireguard_server_public_key.key > $BACKUP_DIR/public_key.$DATE 2>/dev/null; then
    echo "      ✅ Публичный ключ экспортирован"
fi

# 5. Экспорт списка клиентов
echo "[5/5] Экспорт списка клиентов..."
if [ -f /opt/vpn-panel/vpn.db ]; then
    sqlite3 /opt/vpn-panel/vpn.db "SELECT id, phone, client_name, assigned_ip, expires_at, is_enabled FROM clients;" > $BACKUP_DIR/clients_list.$DATE.csv 2>/dev/null
    echo "      ✅ Список клиентов экспортирован"
fi

# Создаём архив
echo ""
echo "Создание архива..."
tar -czf $BACKUP_DIR/backup_$DATE.tar.gz -C $BACKUP_DIR \
    vpn.db.$DATE \
    env.$DATE \
    clientsTable.json.$DATE \
    private_key.$DATE \
    public_key.$DATE \
    clients_list.$DATE.csv 2>/dev/null

# Удаляем временные файлы
rm -f $BACKUP_DIR/*.$DATE

# Выводим информацию
SIZE=$(du -h $BACKUP_DIR/backup_$DATE.tar.gz | cut -f1)
echo ""
echo "========================================="
echo "✅ БЭКАП СОЗДАН!"
echo "========================================="
echo "Файл: $BACKUP_DIR/backup_$DATE.tar.gz"
echo "Размер: $SIZE"
echo "========================================="
echo ""

# Удаляем старые бэкапы (старше 30 дней)
echo "Очистка старых бэкапов (старше 30 дней)..."
find $BACKUP_DIR -name "backup_*.tar.gz" -type f -mtime +30 -delete
echo "✅ Готово!"
