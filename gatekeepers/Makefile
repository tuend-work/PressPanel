CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE
LDFLAGS ?= -Wl,-z,relro,-z,now -pie

MODULES = user cgroup cage vhost db app wp template
PREFIX ?= /opt/wpc
BIN_DIR = $(PREFIX)/bin

.PHONY: all clean install

all: $(MODULES) wpc

wpc: wpc.c
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

%: %.c common.h
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

install: all
	install -m 755 -d $(BIN_DIR)
	@for mod in $(MODULES); do \
		echo "Installing $$mod with SUID to $(BIN_DIR)/$$mod"; \
		install -m 4750 -o root -g wpc $$mod $(BIN_DIR)/$$mod 2>/dev/null || \
		install -m 4750 $$mod $(BIN_DIR)/$$mod; \
	done
	@if [ -f wpc ]; then \
		echo "Installing compiled wpc CLI binary to /usr/local/bin/wpc"; \
		install -m 755 wpc /usr/local/bin/wpc 2>/dev/null || true; \
		ln -sf /usr/local/bin/wpc /usr/bin/wpc 2>/dev/null || true; \
	fi

clean:
	rm -f $(MODULES) wpc *.o
