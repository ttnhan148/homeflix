#!/usr/bin/env bash
# One-time setup for flix.nhant.com: TLS cert, nginx vhost, first deploy, hooks.
# Needs: DNS A record -> this host, docker access, shared proxy in /opt/nginx-proxy.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${DOMAIN:-flix.nhant.com}"
PROXY=/opt/nginx-proxy

cd "$PROXY"
if ! docker compose run --rm --entrypoint sh certbot -c "[ -d /etc/letsencrypt/live/$DOMAIN ]"; then
  docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
    -d "$DOMAIN" --cert-name "$DOMAIN" \
    --email ttnhan148@gmail.com --agree-tos --no-eff-email --non-interactive
fi

cp "$REPO/deploy/nginx-flix.conf" "$PROXY/nginx/conf.d/flix.conf"
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload

"$REPO/deploy/deploy.sh" --force
"$REPO/deploy/install-hooks.sh"
echo "done: https://$DOMAIN"
