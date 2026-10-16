CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE
LDFLAGS ?= -Wl,-z,relro,-z,now -pie

MODULES = user cgroup cage vhost db app wp template
PREFIX ?= /opt/wpc
BIN_DIR = $(PREFIX)/bin

.PHONY: all clean install

all: $(MODULES)

%: %.c common.h
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

install: all
	install -m 755 -d $(BIN_DIR)
	@for mod in $(MODULES); do \
		echo "Installing $$mod with SUID to $(BIN_DIR)/$$mod"; \
		install -m 4750 -o root -g wpc $$mod $(BIN_DIR)/$$mod 2>/dev/null || \
		install -m 4750 $$mod $(BIN_DIR)/$$mod; \
	done

clean:
	rm -f $(MODULES) *.o
