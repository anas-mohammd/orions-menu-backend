#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# OrionMenu — VPS Deployment Script
# Domain: api.orionsmenu.com
#
# Run on the VPS:
#   chmod +x deploy.sh && ./deploy.sh your@email.com
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="api.orionsmenu.com"
EMAIL="${1:-}"
COMPOSE="docker compose"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo -e "\033[0;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[0;32m[OK]\033[0m    $*"; }
err()   { echo -e "\033[0;31m[ERROR]\033[0m $*" >&2; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────
[ -f ".env.production" ] || err ".env.production not found — fill it before deploying."
command -v docker &>/dev/null || err "Docker is not installed."
$COMPOSE version &>/dev/null  || err "Docker Compose plugin not found."

if [ -z "$EMAIL" ]; then
  err "Usage: ./deploy.sh <your-email>\n  Example: ./deploy.sh admin@orionsmenu.com"
fi

# ── Export vars from .env.production for Docker Compose substitution ─────────
# Strip \r (Windows CRLF), skip comments and blank lines, then export
set -a
# shellcheck disable=SC1090
source <(tr -d '\r' < .env.production | grep -v '^\s*#' | grep -v '^\s*$')
set +a
ok "Environment loaded from .env.production"

info "Deploying OrionMenu backend to: https://$DOMAIN"

# ── Step 1: HTTP-only nginx for certbot challenge ─────────────────────────────
info "Starting Nginx (HTTP only) for SSL certificate challenge..."

cat > /tmp/bootstrap.conf <<NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 200 "OrionMenu — SSL setup in progress";
        add_header Content-Type text/plain;
    }
}
NGINXEOF

cp /tmp/bootstrap.conf nginx/conf.d/orionmenu.conf

$COMPOSE up -d nginx certbot mongodb redis
sleep 5

# ── Step 2: Obtain SSL certificate (using docker run directly) ────────────────
info "Requesting SSL certificate from Let's Encrypt..."

# Use docker run directly to avoid docker-compose entrypoint override issues
docker run --rm \
  --network orions-menu-backend_internal \
  -v orions-menu-backend_certbot_www:/var/www/certbot \
  -v orions-menu-backend_certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN"

ok "SSL certificate obtained successfully."

# ── Step 3: Write full nginx config with SSL ──────────────────────────────────
info "Applying full Nginx config with HTTPS..."
cat > nginx/conf.d/orionmenu.conf <<NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    http2 on;
    server_name $DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header X-Frame-Options           "SAMEORIGIN"  always;
    add_header X-Content-Type-Options    "nosniff"     always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

    client_max_body_size 10M;

    location /uploads/ {
        alias /var/www/uploads/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        access_log off;
        try_files \$uri =404;
    }

    location / {
        proxy_pass         http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout    60s;
        proxy_connect_timeout 10s;
        proxy_send_timeout    60s;
    }
}
NGINXEOF

# ── Step 4: Build API image and start all services ───────────────────────────
info "Building API Docker image..."
$COMPOSE build --no-cache api

info "Starting all services..."
$COMPOSE up -d

ok "All containers are up."

# ── Step 5: Reload Nginx with the HTTPS config ───────────────────────────────
sleep 3
$COMPOSE exec nginx nginx -s reload

# ── Step 6: Seed the database ─────────────────────────────────────────────────
info "Seeding database (admin + demo owner accounts)..."
sleep 5
$COMPOSE exec api python scripts/seed.py || info "Seed already applied or skipped."

# ── Step 7: Health check ──────────────────────────────────────────────────────
info "Running health check..."
sleep 3
HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://$DOMAIN/health" || echo "000")

if [ "$HTTP_STATUS" = "200" ]; then
  ok "Health check passed!"
else
  err "Health check returned HTTP $HTTP_STATUS. Run: docker compose logs api"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Deployment Complete!"
echo ""
echo "  🌐  API:     https://api.orionsmenu.com"
echo "  📖  Docs:    https://api.orionsmenu.com/docs"
echo "  ❤️   Health:  https://api.orionsmenu.com/health"
echo ""
echo "  Useful commands:"
echo "    make logs svc=api     → tail API logs"
echo "    make seed             → re-run seed"
echo "    make backup           → backup MongoDB"
echo "    make ssl-renew        → force SSL renewal"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
