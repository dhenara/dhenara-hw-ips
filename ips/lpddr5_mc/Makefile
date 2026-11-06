.PHONY: prepare check format

prepare:
	python3 ips/lpddr5_mc/tools/prepare_tools.py

check:
	python3 ips/lpddr5_mc/tools/check.py --report "$(REPORT)"

format:
	python3 ips/lpddr5_mc/tools/check.py --format
