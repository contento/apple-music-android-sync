# Standalone build of Rockbox's ipodpatcher (macOS / Linux).
# The upstream Makefile needs the whole Rockbox tree; this one does not.
#   make            → ./ipodpatcher
#   make clean
CC      ?= cc
CFLAGS  += -O2 -Wall -W
# Upstream passes the version in from its build system (libtools.make).
VERSION ?= rockbox-eafcc2c
CFLAGS  += -DVERSION='"$(VERSION)"'
SOURCES  = main.c ipodpatcher.c fat32format.c arc4.c ipodio-posix.c ipodpatcher_aupd.c
LDFLAGS_Darwin = -framework CoreFoundation -framework IOKit
LDFLAGS += $(LDFLAGS_$(shell uname -s))

ipodpatcher: $(SOURCES) *.h
	$(CC) $(CFLAGS) -o $@ $(SOURCES) $(LDFLAGS)

clean:
	rm -f ipodpatcher

.PHONY: clean
