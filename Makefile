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
# Contributors can run the complete public suite with its pinned tools or use
# an installed regression binary to select a manifest test or test list.
ifneq ($(strip $(TEST)),)
ifneq ($(strip $(TEST_LIST)),)
$(error Select either TEST or TEST_LIST)
endif
regress: run-test
else ifneq ($(strip $(TEST_LIST)),)
regress: run-testlist
else
regress: check-controller
endif

HW_REGRESS ?= hw-regress
SEED ?= 2095
export HW_REGRESS SEED TARGET TEST TEST_LIST ARTIFACT_DIR BUILD_DIR
REGRESS_CLI = "$$HW_REGRESS" --manifest hw-regress.json --output-dir "$$ARTIFACT_DIR" --seed "$$SEED" $(if $(strip $(BUILD_DIR)),--build-dir "$$BUILD_DIR")

# The Debian package extracted by make prepare needs its relocatable runtime.
# With a user-selected or system Verilator, preserve its configured environment.
ifeq ($(origin VERILATOR),undefined)
LPDDR5_VERILATOR := $(DHENARA_AGENT_TOOL_STORAGE_ROOT)/lpddr5-contracts/verilator-5.006-3/usr
ifneq ($(wildcard $(LPDDR5_VERILATOR)/bin/verilator),)
compile run-test run-testlist: export VERILATOR := $(LPDDR5_VERILATOR)/bin/verilator
compile run-test run-testlist: export VERILATOR_ROOT := $(LPDDR5_VERILATOR)/share/verilator
compile run-test run-testlist: export VERILATOR_BIN := ../../bin/verilator_bin
endif
endif

.PHONY: list-tests compile run-test run-testlist
list-tests:
	@"$$HW_REGRESS" --manifest hw-regress.json list

compile:
	@: "$${TARGET:?Set TARGET to a testbench target}"
	@$(REGRESS_CLI) compile "$$TARGET"

run-test:
	@: "$${TEST:?Set TEST to one test ID}"
	@$(REGRESS_CLI) run-test "$$TEST"

run-testlist:
	@: "$${TEST_LIST:?Set TEST_LIST to a named test list}"
	@$(REGRESS_CLI) run-testlist "$$TEST_LIST"
