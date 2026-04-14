#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# OrionMenu — VPS Deployment Script
# Run on the VPS after cloning the repo:
#   chmod +x deploy.sh && ./deploy.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="${1:-}"          # First arg: your domain, e.g. api.orionmenu.com
EMAIL="${2:-}"           # Second arg: email for Let's Encrypt alerts
COMPOSE="docker compose"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo -e "\033[0;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[0;32m[OK]\033[0m    $*"; }
err()   { echo -e "\033[0;31m[ERROR]\033[0m $*" >&2; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────
[ -f ".env.production" ] || err ".env.production not found. Copy and fill it before deploying."
command -v docker &>/dev/null || err "Docker is not installed."
$COMPOSE version &>/dev/null  || err "Docker Compose plugin not found."

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  err "Usage: ./deploy.sh <domain> <email>\n  Example: ./deploy.sh api.orionmenu.com admin@orionmenu.com"
fi

# ── Replace placeholder domain in nginx config ───────────────────────────────
info "Configuring Nginx for domain: $DOMAIN"
sed -i "s/YOUR_DOMAIN/$DOMAIN/g" nginx/conf.d/orionmenu.conf

# ── Step 1: Start with HTTP-only nginx for certbot challenge ─────────────────
info "Starting services (HTTP only for initial SSL setup)..."

# Temporarily use a minimal nginx config that only serves the certbot challenge
cat > nginx/conf.d/orionmenu.conf.tmp <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 200 "OrionMenu — awaiting SSL setup";
        add_header Content-Type text/plain;
    }
}
EOF
mv nginx/conf.d/orionmenu.conf.tmp nginx/conf.d/orionmenu.conf.bootstrap 2>/dev/null || true

$COMPOSE up -d nginx certbot mongodb redis

# ── Step 2: Obtain SSL certificate ───────────────────────────────────────────
info "Obtaining SSL certificate for $DOMAIN..."
$COMPOSE run --rm certbot certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

ok "SSL certificate obtained."

# ── Step 3: Write full nginx config with SSL ──────────────────────────────────
info "Writing full Nginx config with SSL..."
sed "s/YOUR_DOMAIN/$DOMAIN/g" nginx/conf.d/orionmenu.conf > nginx/conf.d/orionmenu.conf
# Remove bootstrap config if it exists
rm -f nginx/conf.d/orionmenu.conf.bootstrap

# ── Step 4: Build and start all services ─────────────────────────────────────
info "Building API image..."
$COMPOSE build --no-cache api

info "Starting all services..."
$COMPOSE up -d

ok "All services started."

# ── Step 5: Seed the database ─────────────────────────────────────────────────
info "Seeding database (admin + demo owner)..."
$COMPOSE exec api python scripts/seed.py || true

# ── Step 6: Health check ──────────────────────────────────────────────────────
info "Running health check..."
sleep 5
HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://$DOMAIN/health" || echo "000")

if [ "$HTTP_STATUS" = "200" ]; then
  ok "Health check passed — API is live at https://$DOMAIN"
else
  err "Health check failed (HTTP $HTTP_STATUS). Check logs: docker compose logs api"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deployment complete!"
echo "  API:     https://$DOMAIN"
echo "  Docs:    https://$DOMAIN/docs"
echo "  Logs:    docker compose logs -f api"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
