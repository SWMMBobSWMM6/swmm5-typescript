# tools/libm: glibc's math routines for the TypeScript and Java ports

EPA's runswmm calls glibc for `exp`, `log`, `log10`, `pow`, `sin`, `cos`, `tan`, `asin`, `acos` and `atan`.
On x86-64, glibc 2.39 picks, at program start, a version of most of these compiled with `-mfma -mavx2`
when the processor has fused multiply-add; the generic version rounds differently in the last bit for some
arguments. Running EPA on the generic versions (`GLIBC_TUNABLES=glibc.cpu.hwcaps=-AVX2,-FMA,-FMA4`) changes
43 of the 480 gate models (`gate/ctl_libm_generic.txt`).

C, C++, Fortran, C#, Python and Rust call the system libm and inherit that choice. JavaScript and Java bring
their own math functions, which differ from glibc's, so the TypeScript and Java ports carry translations of
glibc's FMA builds, made here:

| Step | File | What it does |
|---|---|---|
| census | `census.c`, `run_census.sh`, `census_summary.py` | LD_PRELOAD interposer: counts every libm call EPA makes on the 480 models and samples their arguments (`gate/results/libm_census.txt`). pow: 1.24 billion calls; sin, cos: 136 million each. |
| compile | `build_glibm.sh` | compiles glibc 2.39's `sysdeps/ieee754/dbl-64` sources with glibc's flags for the FMA variants and dumps GCC's optimized GIMPLE, in which every fused multiply-add GCC chose is explicit (`.FMA`, `.FMS`, `.FNMA`). The objects are only compiled, never run. |
| tables | `extract_tables.py` | copies the constant tables (`__exp_data`, `__pow_log_data`, the sin/cos table, ...) out of the object files with objdump/objcopy, checking each symbol's size against glibc's struct layout. |
| translate | `gim2x.py` | GIMPLE to TypeScript or Java: each function becomes a loop over its basic blocks with PHI nodes as parallel copies; 64-bit integers are BigInt in TypeScript and long in Java. Appends glibc's SVID wrappers for log10/asin/acos and, for TypeScript, a hand-written fast path of pow. |
| runtime | `glibm_rt.ts`, `GlibmRt.java` | exactly rounded fma (Java: `Math.fma`; TypeScript: Dekker's product and Boldo-Melquiond's round-to-odd, with an exact BigInt fallback), bit casts, x86's float-to-int rule, glibc's error helpers, and a port of `branred.c`. |
| check | `test/run_tests.sh` | compares both ports bit for bit with the system libm on millions of arguments: random ones over SWMM's ranges, the census samples, and edge cases. |

`make_libm.sh GLIBC_SRC` runs compile, tables and translate, writing `ts/src/glibm.ts` and
`java/src/swmm5/Glibm.java`. GLIBC_SRC is a glibc checkout; the one used here is the 2.39 stable branch
(`release/2.39/master`) at commit ce65d944e38a (20 January 2026).

Result of `test/run_tests.sh` with the census samples (saved as `gate/results/libm_tests.txt`): 18.7 million
random and census arguments plus 3.3 million edge cases, 22.0 million in all per language, gave 0 differences
and 0 NaN-sign differences for every function (and for fma) in both languages, and 0 differences between
TypeScript's pow fast path and its full translation on 20 million further arguments.

What this does not cover: it reproduces glibc's FMA builds, which glibc selects on x86-64 processors with
both FMA and AVX2 (Intel since Haswell, AMD since Excavator). The TypeScript and Java ports give those results
on any machine, so they match EPA run on such a processor and not EPA run on one without (where glibc picks
its FMA4, AVX or SSE2 build instead). The
translations are derived from glibc and so are covered by glibc's licence, LGPL-2.1-or-later.

Written by Claude (Anthropic); see AI-USE.md.
