#!/usr/bin/env bash
# Run this on the server after cloning the repo.
# Usage: bash deploy.sh yourdomain.com your@email.com
set -e

DOMAIN=${1:?usage: deploy.sh <domain> <email>}
EMAIL=${2:?usage: deploy.sh <domain> <email>}

# 1. Install Docker if needed
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
if ! command -v docker compose &>/dev/null; then
  apt-get install -y docker-compose-plugin
fi

# 2. Create data directories
mkdir -p data/uploads data/generated_tex data/certbot/conf data/certbot/www
touch data/jobs.db

# 3. Create .env if it doesn't exist
if [ ! -f .env ]; then
  cp .env.example .env
  SECRET=$(openssl rand -hex 32)
  sed -i "s/change-me-use-openssl-rand-hex-32/$SECRET/" .env
  echo "⚠️  Edit .env and fill in OPENAI_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, FRONTEND_URL"
  exit 1
fi

# 4. Patch nginx config with real domain
sed -i "s/server_name _;/server_name $DOMAIN;/" nginx/nginx.conf

# 5. Start services (HTTP only first so certbot can verify)
docker compose up -d --build

# 6. Get SSL certificate
docker run --rm \
  -v "$(pwd)/data/certbot/conf:/etc/letsencrypt" \
  -v "$(pwd)/data/certbot/www:/var/www/certbot" \
  certbot/certbot certonly --webroot \
    --webroot-path /var/www/certbot \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos --non-interactive

# 7. Append HTTPS block to nginx config
cat >> nginx/nginx.conf << NGINX

server {
    listen 443 ssl;
    server_name $DOMAIN;
    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    location = / {
        root /usr/share/nginx/html;
        try_files /index.html =404;
    }
    location = /app.js {
        root /usr/share/nginx/html;
        try_files /app.js =404;
    }
    location / {
        proxy_pass         http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        client_max_body_size 12M;
    }
}
NGINX

docker compose restart nginx
echo "✅ Deployed to https://$DOMAIN"
