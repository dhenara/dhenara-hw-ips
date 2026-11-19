.PHONY: prepare check check-controller format generate

BUILD_SUBDIR ?= contract-smoke
SUITE ?= all

prepare:
	python3 ips/lpddr5_mc/tools/prepare_tools.py

check:
	python3 ips/lpddr5_mc/tools/check.py --report "$(REPORT)" --build-subdir "$(BUILD_SUBDIR)"

check-controller:
	python3 ips/lpddr5_mc/tools/check_controller.py --evidence "$(EVIDENCE)" --build-name "$(BUILD_SUBDIR)" --suite "$(SUITE)" $(foreach step,$(STEPS),--step "$(step)") $(CHECK_ARGS)

format:
	python3 ips/lpddr5_mc/tools/check.py --format

generate:
	python3 ips/lpddr5_mc/tools/generate.py
