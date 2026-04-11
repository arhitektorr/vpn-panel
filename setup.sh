#!/bin/bash
# setup.sh - Запустить один раз на новом сервере

set -e

echo "🚀 Установка VPN Panel..."

# Обновление системы
apt update && apt upgrade -y

# Установка Python и зависимостей
apt install -y python3 python3-pip python3-venv sqlite3 nginx

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка Python пакетов
pip install --upgrade pip
pip install -r requirements.txt

# Копирование .env из примера
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️  Отредактируйте .env файл: nano .env"
fi

# Настройка systemd сервиса
cp vpn-panel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable vpn-panel
systemctl start vpn-panel

# Настройка Nginx (опционально)
if [ -f nginx.conf ]; then
    cp nginx.conf /etc/nginx/sites-available/vpn-panel
    ln -s /etc/nginx/sites-available/vpn-panel /etc/nginx/sites-enabled/
    systemctl restart nginx
fi

echo "✅ Готово!"
echo "Проверьте статус: systemctl status vpn-panel"
echo "Логи: journalctl -u vpn-panel -f"
