.PHONY: prepare check format generate

BUILD_SUBDIR ?= contract-smoke

prepare:
	python3 ips/lpddr5_mc/tools/prepare_tools.py

check:
	python3 ips/lpddr5_mc/tools/check.py --report "$(REPORT)" --build-subdir "$(BUILD_SUBDIR)"

format:
	python3 ips/lpddr5_mc/tools/check.py --format

generate:
	python3 ips/lpddr5_mc/tools/generate.py
