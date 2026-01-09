.PHONY: help build clean

PYTHON ?= python3
VERSION ?= 1.21.11
CACHE_DIR ?= .cache

help:
	@printf "make build VERSION=<mc-version>   Build assets from the official client jar\\n"
	@printf "make clean VERSION=<mc-version>   Remove generated data for version\\n"

build:
	$(PYTHON) scripts/build_version.py --version $(VERSION) --cache-dir $(CACHE_DIR) --data-dir data --force

clean:
	rm -rf data/$(VERSION)
