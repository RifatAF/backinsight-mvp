#!/usr/bin/env bash
# Установка собственного TURN-сервера (coturn) для BackInsight Live на чистый Ubuntu 22.04/24.04 VPS.
# Нужен домен (A-запись на IP сервера), открытые порты 80/tcp, 443/tcp+udp, 3478/tcp+udp, 49152-65535/udp.
# Запуск: sudo bash coturn-setup.sh turn.example.ru you@example.ru
set -euo pipefail

DOMAIN="${1:?Укажите домен, например turn.example.ru}"
EMAIL="${2:?Укажите email для сертификата Let's Encrypt}"
TURN_USER="bi"
TURN_PASS="$(openssl rand -hex 16)"

apt-get update
apt-get install -y coturn certbot

# Внешний IP сервера: coturn сообщает его клиентам в relay-кандидатах.
EXT_IP="$(curl -4 -fsS https://ifconfig.me || hostname -I | awk '{print $1}')"

# Сертификат для TURNS на 443 (трафик неотличим от HTTPS).
certbot certonly --standalone -d "$DOMAIN" -m "$EMAIL" --agree-tos -n

# coturn работает от пользователя turnserver: копируем сертификаты в его каталог и обновляем при продлении.
install -d -o turnserver -g turnserver -m 750 /etc/coturn/certs
cat > /etc/letsencrypt/renewal-hooks/deploy/coturn.sh <<HOOK
#!/bin/sh
install -o turnserver -g turnserver -m 640 /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/coturn/certs/fullchain.pem
install -o turnserver -g turnserver -m 640 /etc/letsencrypt/live/$DOMAIN/privkey.pem /etc/coturn/certs/privkey.pem
systemctl restart coturn
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/coturn.sh
install -o turnserver -g turnserver -m 640 "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" /etc/coturn/certs/fullchain.pem
install -o turnserver -g turnserver -m 640 "/etc/letsencrypt/live/$DOMAIN/privkey.pem" /etc/coturn/certs/privkey.pem

cat > /etc/turnserver.conf <<CONF
listening-port=3478
tls-listening-port=443
alt-tls-listening-port=5349
external-ip=$EXT_IP
realm=$DOMAIN
server-name=$DOMAIN
fingerprint
lt-cred-mech
user=$TURN_USER:$TURN_PASS
cert=/etc/coturn/certs/fullchain.pem
pkey=/etc/coturn/certs/privkey.pem
min-port=49152
max-port=65535
no-cli
no-multicast-peers
no-loopback-peers
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
user-quota=12
total-quota=200
stale-nonce=600
log-file=syslog
CONF

# Порт 443 ниже 1024: разрешаем coturn слушать его без root.
setcap cap_net_bind_service=+ep "$(command -v turnserver)"
sed -i 's/^#\?TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn 2>/dev/null || true

if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp; ufw allow 443/tcp; ufw allow 443/udp
  ufw allow 3478/tcp; ufw allow 3478/udp; ufw allow 49152:65535/udp
fi

systemctl enable coturn
systemctl restart coturn

echo
echo "Готово. Вставьте в index.html вместо 'const OWN_TURN = null;':"
echo "  const OWN_TURN = { host: '$DOMAIN', username: '$TURN_USER', credential: '$TURN_PASS' };"
