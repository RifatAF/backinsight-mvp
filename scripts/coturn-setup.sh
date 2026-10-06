#!/usr/bin/env bash
# Установка собственного TURN-сервера (coturn) для BackInsight Live на чистый Ubuntu 22.04/24.04 VPS.
# Нужен домен (A-запись на IP сервера), открытые порты 80/tcp, 443/tcp+udp, 3478/tcp+udp, 49152-65535/udp.
# Запуск: sudo bash coturn-setup.sh turn.example.ru you@example.ru [пароль] [публичный IP]
# Если сертификат не выдался (например, лимит Let's Encrypt), сервер всё равно запустится на 3478 UDP/TCP без TLS.
set -euo pipefail

DOMAIN="${1:?Укажите домен, например turn.example.ru}"
EMAIL="${2:?Укажите email для сертификата}"
TURN_USER="bi"
TURN_PASS="${3:-$(openssl rand -hex 16)}"

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "== Шаг 1/4: установка coturn и certbot"
apt-get update -y
apt-get install -y coturn certbot curl
command -v turnserver >/dev/null || { echo "ОШИБКА: coturn не установился"; exit 1; }

echo "== Шаг 2/4: определение адресов"
# Публичный IP (его coturn сообщает клиентам) и внутренний IP (у облачных VPS часто стоит NAT).
EXT_IP="${4:-}"
if [ -z "$EXT_IP" ]; then EXT_IP="$(curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true)"; fi
if [ -z "$EXT_IP" ]; then EXT_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"; fi
PRIV_IP="$(hostname -I | awk '{print $1}')"
if [ -z "$EXT_IP" ]; then EXT_IP="$PRIV_IP"; fi
if [ "$EXT_IP" != "$PRIV_IP" ]; then EXT_MAP="$EXT_IP/$PRIV_IP"; else EXT_MAP="$EXT_IP"; fi
echo "Публичный IP: $EXT_IP, внутренний: $PRIV_IP"

echo "== Шаг 3/4: сертификат"
# Сертификат для TURNS на 443 (трафик неотличим от HTTPS).
TLS=0
if command -v certbot >/dev/null && certbot certonly --standalone -d "$DOMAIN" -m "$EMAIL" --agree-tos -n; then
  TLS=1
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
else
  echo "ВНИМАНИЕ: сертификат не получен, TURNS на 443 будет выключен, работает TURN на 3478 UDP/TCP."
fi

cat > /etc/turnserver.conf <<CONF
listening-port=3478
external-ip=$EXT_MAP
realm=$DOMAIN
server-name=$DOMAIN
fingerprint
lt-cred-mech
user=$TURN_USER:$TURN_PASS
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

if [ "$TLS" = 1 ]; then
  cat >> /etc/turnserver.conf <<CONF
tls-listening-port=443
alt-tls-listening-port=5349
cert=/etc/coturn/certs/fullchain.pem
pkey=/etc/coturn/certs/privkey.pem
CONF
fi

echo "== Шаг 4/4: запуск"
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
systemctl --no-pager --lines=0 status coturn | head -3 || true
echo "TLS: $([ "$TLS" = 1 ] && echo включён || echo выключен)"
echo "Готово. Строка для index.html (если ещё не вставлена):"
echo "  const OWN_TURN = { host: '$DOMAIN', username: '$TURN_USER', credential: '$TURN_PASS' };"
