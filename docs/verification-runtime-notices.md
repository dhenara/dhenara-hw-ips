# Verification runtime notices

The project RTL, checkers and documentation are distributed under Apache-2.0. Third-party runtime components and retained upstream source references keep their own licenses; they are not relicensed under Apache-2.0.

## Verilator 5.006

Copyright 2003–2023 Wilson Snyder. The retained simulation executables incorporate Verilator runtime components (`verilated.cpp`, `verilated_threads.cpp`, and, where used, timing and DPI components). For those components this distribution uses the **Artistic License 2.0** option offered by Verilator. The complete [Artistic License](verification-runtime-licenses/Verilator-Artistic-2.0.txt) accompanies the evidence.

The final package contains fixed standalone simulation executables. Its build metadata disables VCD/FST tracing and SystemC. The main final archive is `lpddr5-initial-ba8f514-evidence.tar.gz`, SHA-256 `53cf115163d26333aa97ef38fa1be54324029d95b3541506658d26a8106c8b6e`; the same component notices accompany the supporting qualification evidence.

The supporting archive also retains 16 Verilator source references with their original Wilson Snyder copyright and `LGPL-3.0-only OR Artistic-2.0` headers. This distribution uses the Artistic License 2.0 option for these copies too. Fifteen are under `runtime-source/include/` in `lpddr5/qualification/master-refresh-8c576f8/evidence.tar.gz`: `verilated.cpp`, `verilated.h`, `verilated_config.h`, `verilated_funcs.h`, `verilated_sym_props.h`, `verilated_syms.h`, `verilated_types.h`, `verilatedos.h`, `verilated_imp.h`, `verilated_threads.cpp`, `verilated_threads.h`, `verilated_timing.cpp`, `verilated_timing.h`, `verilated_trace.h` and `verilated_trace_defs.h`. The remaining reference is `references/Verilator-v5.006-V3File.cpp` in the split `lpddr5/qualification/power-pd-505c5cd/evidence.tar.gz` package. Per-file copyright years and upstream attribution remain in those original files.

The exact Debian Verilator 5.006-3 source is available as [upstream source](https://deb.debian.org/debian/pool/main/v/verilator/verilator_5.006.orig.tar.gz), [Debian patches](https://deb.debian.org/debian/pool/main/v/verilator/verilator_5.006-3.debian.tar.xz) and [source descriptor](https://deb.debian.org/debian/pool/main/v/verilator/verilator_5.006-3.dsc). Download all three together and use `dpkg-source -x verilator_5.006-3.dsc`. The [version-specific upstream runtime](https://github.com/verilator/verilator/tree/v5.006/include) and [licensing explanation](https://github.com/verilator/verilator/blob/v5.006/docs/guide/faq.rst) identify the original component terms.

## GCC 12.2 and C++ runtime/header material

GCC and GNU C++ library runtime/header material retain their applicable copyright notices, [GPL version 3 text](verification-runtime-licenses/GCC-COPYING3.txt) and [GCC Runtime Library Exception 3.1](verification-runtime-licenses/GCC-Runtime-Exception-3.1.txt). The [C++ library notices](verification-runtime-licenses/GCC-CXX-NOTICES.txt) retain the Free Software Foundation, Hewlett-Packard and Silicon Graphics attribution and permissions from the versioned library header.

The final executables dynamically depend on the host's `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1` and `libc.so.6`; these shared libraries are not included in the final archive. The exception and embedded-runtime terms do not change the Apache-2.0 license of Dhenara's own controller and verification code. Source and full runtime terms are available from [GCC 12.2](https://github.com/gcc-mirror/gcc/tree/releases/gcc-12.2.0) and the [GNU C++ library licensing guide](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html).

## Distribution

The GitHub Release includes `verification-runtime-notices.zip`, containing this notice, the component license texts, the project Apache-2.0 license and project NOTICE. Keep that companion with copies of the retained evidence, including its simulator binaries and upstream source references. External JEDEC/DFI standards and separately downloaded tools retain their own terms and are not licensed by the project's Apache-2.0 grant.
