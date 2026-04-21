# OrionMenu — Makefile for common VPS operations
# Usage: make <target>

COMPOSE = docker compose

.PHONY: up down build update restart logs seed backup shell-api shell-mongo

## Start all containers
up:
	$(COMPOSE) up -d

## Safe update: rebuild + restart API only — DB and Redis are NEVER touched
update:
	git pull origin main
	$(COMPOSE) build api
	$(COMPOSE) up -d --no-deps api
	docker image prune -f
	$(COMPOSE) ps

## Stop all containers
down:
	$(COMPOSE) down

## Rebuild API image and restart
build:
	$(COMPOSE) build --no-cache api
	$(COMPOSE) up -d api

## Restart a specific service (usage: make restart svc=api)
restart:
	$(COMPOSE) restart $(svc)

## Tail logs (usage: make logs | make logs svc=api)
logs:
	$(COMPOSE) logs -f $(svc)

## Seed the database
seed:
	$(COMPOSE) exec api python scripts/seed.py

## Backup MongoDB to ./backups/
backup:
	mkdir -p backups
	$(COMPOSE) exec mongodb mongodump \
	  --uri="$$MONGODB_URL" \
	  --out=/tmp/dump
	$(COMPOSE) cp mongodb:/tmp/dump ./backups/dump_$$(date +%Y%m%d_%H%M%S)
	@echo "Backup saved to ./backups/"

## Open a shell inside the API container
shell-api:
	$(COMPOSE) exec api /bin/bash

## Open mongosh inside the MongoDB container
shell-mongo:
	$(COMPOSE) exec mongodb mongosh "$$MONGODB_URL"

## Show running containers and their status
ps:
	$(COMPOSE) ps

## Force-renew SSL certificates
ssl-renew:
	$(COMPOSE) run --rm certbot certbot renew --force-renewal
	$(COMPOSE) restart nginx
