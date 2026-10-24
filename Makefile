SHELL := /bin/bash
PY ?= python3
COMPOSE ?= docker compose
ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

.PHONY: help init render up down restart logs ps test doctor panel forward forward-apply cert backup shell console-logs clean

help:
	@echo "make init         create the channel inventory"
	@echo "make render       render xray + sing-box configs from the inventory"
	@echo "make up           render, then start the stack"
	@echo "make down         stop the stack"
	@echo "make logs         follow logs"
	@echo "make test         unit tests + xray config test"
	@echo "make doctor       host and deployment checks"
	@echo "make panel        route panel traffic through the gateway"
	@echo "make forward      render nftables forwarding rules"
	@echo "make forward-apply apply the generated nftables rules"
	@echo "make cert DOMAIN=x issue/renew a certificate for hysteria2"
	@echo "make backup       archive state into backups/"
	@echo "make console-logs follow the web console logs"
	@echo "make shell        shell inside the gateway container"

init:
	$(PY) -m outlander init

render:
	$(PY) -m outlander render

up: render
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) down
	$(COMPOSE) up -d

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test: render
	$(PY) -m unittest discover -s tests -v
	$(PY) -m outlander test

doctor:
	$(PY) -m outlander doctor

panel:
	@if [ "$$(id -u)" = "0" ]; then $(PY) -m outlander panel patch; else sudo $(PY) -m outlander panel patch; fi

forward:
	$(PY) -m outlander forward render

forward-apply: forward
	sudo bash scripts/forward-apply.sh

cert:
	@test -n "$(DOMAIN)" || (echo "usage: make cert DOMAIN=hy2.example.com" && exit 1)
	sudo bash scripts/cert.sh $(DOMAIN)

backup:
	sudo bash scripts/backup.sh

shell:
	docker exec -it ol-gateway /bin/sh

console-logs:
	$(COMPOSE) logs -f --tail=100 console

clean:
	rm -f config/xray/config.json config/sing-box/config.json config/nftables/outlander.nft
