.PHONY: all gatekeepers venv frontend install clean help

SHELL := /bin/bash
PREFIX ?= /opt/presspanel
PYTHON ?= python3

all: gatekeepers frontend

help:
	@echo "PressPanel Build Commands:"
	@echo "  make gatekeepers   - Biên dịch 8 binary C gatekeeper"
	@echo "  make venv          - Khởi tạo môi trường ảo Python venv và cài dependencies"
	@echo "  make frontend      - Cài đặt và build giao diện Vue 3"
	@echo "  make install       - Cài đặt toàn bộ hệ thống vào $(PREFIX)"
	@echo "  make clean         - Dọn dẹp các tệp build tạm thời"

gatekeepers:
	@echo "==> Đang biên dịch các binary C gatekeeper..."
	$(MAKE) -C gatekeepers all

venv:
	@echo "==> Đang khởi tạo môi trường Python venv..."
	$(PYTHON) -m venv venv
	./venv/bin/pip install --upgrade pip
	./venv/bin/pip install -r requirements.txt

frontend:
	@echo "==> Đang build giao diện web Vue 3..."
	@if [ -d "web" ] && [ -f "web/package.json" ]; then \
		cd web && npm install && npm run build; \
	else \
		echo "Bỏ qua frontend (chưa khởi tạo mã nguồn Vue 3)"; \
	fi

install: gatekeepers
	@echo "==> Đang cài đặt PressPanel vào $(PREFIX)..."
	mkdir -p $(PREFIX)/{bin,app,conf,static,data,logs/tasks,scripts/apps,server,templates}
	install -m 755 -d $(PREFIX)/bin
	$(MAKE) -C gatekeepers install PREFIX=$(PREFIX)
	cp -r app $(PREFIX)/
	cp -r scripts/* $(PREFIX)/scripts/ 2>/dev/null || true
	cp -r templates/* $(PREFIX)/templates/ 2>/dev/null || true
	@if [ ! -f "$(PREFIX)/conf/presspanel.conf" ] && [ -f "conf/presspanel.conf" ]; then \
		cp conf/presspanel.conf $(PREFIX)/conf/; \
		chmod 640 $(PREFIX)/conf/presspanel.conf; \
	fi
	@if [ -d "web/dist" ]; then cp -r web/dist/* $(PREFIX)/static/ 2>/dev/null || true; fi
	@echo "==> Hoàn tất cài đặt PressPanel!"

clean:
	$(MAKE) -C gatekeepers clean
	rm -rf venv web/dist
