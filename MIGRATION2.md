# 1. Клонируем репозиторий
git clone https://github.com/arhitektorr/vpn-panel.git /opt/vpn-panel
cd /opt/vpn-panel

# 2. Устанавливаем зависимости
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Восстанавливаем только БД и .env (без ключей)
# (нужно распаковать бэкап вручную и скопировать только vpn.db и .env)

# 4. Перевыпускаем ключи для всех клиентов
python3 reissue_keys.py

# 5. Запускаем панель
sudo systemctl daemon-reload
sudo systemctl enable vpn-panel
sudo systemctl start vpn-panel
