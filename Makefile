.DEFAULT_GOAL := regress

# A fresh name keeps independent invocations from reusing builds or evidence.
ifndef BUILD_SUBDIR
BUILD_SUBDIR := lpddr5-$(shell python3 -c 'from uuid import uuid4; print(uuid4().hex)')
endif
ARTIFACT_DIR ?= $(CURDIR)/ips/lpddr5_mc/evidence/$(BUILD_SUBDIR)
EVIDENCE ?= $(ARTIFACT_DIR)
export DHENARA_AGENT_TOOL_STORAGE_ROOT ?= $(CURDIR)/out/tools

include ips/lpddr5_mc/Makefile

.PHONY: regress
regress: check-controller
