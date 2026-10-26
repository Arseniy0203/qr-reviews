#!/usr/bin/env sh
# First-time HTTPS setup for production.
#
# nginx cannot start without a certificate, and certbot needs nginx to answer
# the ACME challenge. So: create a temporary self-signed cert, start nginx,
# request the real certificate via webroot, then reload nginx.
#
# Usage (from repo root, on the server):  sh infra/scripts/init-letsencrypt.sh
# Set LETSENCRYPT_STAGING=1 to test against the staging CA (no rate limits).
set -eu

COMPOSE="docker compose -f docker-compose.prod.yml"

# shellcheck disable=SC1091
[ -f .env ] && . ./.env
: "${DOMAIN:?DOMAIN is not set in .env}"
: "${LETSENCRYPT_EMAIL:?LETSENCRYPT_EMAIL is not set in .env}"

LIVE_DIR="/etc/letsencrypt/live/$DOMAIN"

echo ">>> Creating temporary self-signed certificate for $DOMAIN"
$COMPOSE run --rm --entrypoint sh certbot -c "\
  apk add --no-cache openssl >/dev/null && \
  mkdir -p $LIVE_DIR && \
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout $LIVE_DIR/privkey.pem -out $LIVE_DIR/fullchain.pem -subj /CN=localhost"

echo ">>> Starting nginx"
$COMPOSE up -d --build nginx

echo ">>> Removing temporary certificate"
$COMPOSE run --rm --entrypoint sh certbot -c "\
  rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN \
         /etc/letsencrypt/renewal/$DOMAIN.conf"

STAGING_ARG=""
[ "${LETSENCRYPT_STAGING:-0}" = "1" ] && STAGING_ARG="--staging"

echo ">>> Requesting Let's Encrypt certificate"
$COMPOSE run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
  $STAGING_ARG --email "$LETSENCRYPT_EMAIL" --agree-tos --no-eff-email \
  -d "$DOMAIN" --rsa-key-size 4096

echo ">>> Reloading nginx"
$COMPOSE exec nginx nginx -s reload
echo ">>> Done. Renewal runs automatically in the certbot service."
