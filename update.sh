#!/bin/bash
# update.sh - Запускать после git pull

set -e

echo "🔄 Обновление VPN Panel..."

cd /opt/vpn-panel

# Активация venv
source venv/bin/activate

# Обновление зависимостей
pip install -r requirements.txt

# Перезапуск сервиса
systemctl restart vpn-panel

# Проверка статуса
systemctl status vpn-panel --no-pager

echo "✅ Обновлено!"
echo "Логи: journalctl -u vpn-panel -f"
