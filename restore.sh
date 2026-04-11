#!/bin/bash
# restore.sh - Восстановить на новом сервере

if [ -z "$1" ]; then
    echo "Использование: ./restore.sh backup-file.tar.gz"
    exit 1
fi

BACKUP_FILE=$1
RESTORE_DIR="/opt/vpn-panel"

echo "🔄 Восстановление из $BACKUP_FILE..."

# Распаковка
sudo tar -xzf $BACKUP_FILE -C /opt/

# Установка зависимостей
cd $RESTORE_DIR
./setup.sh

echo "✅ Восстановлено!"
