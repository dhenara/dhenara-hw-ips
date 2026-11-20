HW_REGRESS ?= hw-regress
SEED ?= 2095
HW_REGRESS_MANIFEST := $(dir $(lastword $(MAKEFILE_LIST)))hw-regress.yaml
export HW_REGRESS SEED TARGET TEST TEST_LIST ARTIFACT_DIR BUILD_DIR INVENTORY
REGRESS_CLI = "$$HW_REGRESS" --manifest "$(HW_REGRESS_MANIFEST)" --output-dir "$$ARTIFACT_DIR" --seed "$$SEED" $(if $(strip $(BUILD_DIR)),--build-dir "$$BUILD_DIR")

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
	@"$$HW_REGRESS" --manifest "$(HW_REGRESS_MANIFEST)" list $(if $(strip $(INVENTORY)),--output "$$INVENTORY")

compile:
	@: "$${TARGET:?Set TARGET to a testbench target}"
	@$(REGRESS_CLI) compile "$$TARGET"

run-test:
	@: "$${TEST:?Set TEST to one test ID}"
	@$(REGRESS_CLI) run-test "$$TEST"

run-testlist:
	@: "$${TEST_LIST:?Set TEST_LIST to a named test list}"
	@$(REGRESS_CLI) run-testlist "$$TEST_LIST"
