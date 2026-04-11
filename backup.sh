#!/bin/bash
# backup.sh - Создать бэкап для переноса

BACKUP_DIR="vpn-panel-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p $BACKUP_DIR

echo "📦 Создание бэкапа в $BACKUP_DIR..."

# Копирование файлов проекта (без venv и __pycache__)
rsync -av --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.git' --exclude='backups' \
    ./ $BACKUP_DIR/

# Создание архива
tar -czf $BACKUP_DIR.tar.gz $BACKUP_DIR/
rm -rf $BACKUP_DIR/

echo "✅ Бэкап создан: $BACKUP_DIR.tar.gz"
echo "Размер: $(du -h $BACKUP_DIR.tar.gz | cut -f1)"
