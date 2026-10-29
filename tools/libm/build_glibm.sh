#!/bin/bash
# build_glibm.sh GLIBC_SRC OUTDIR
# Compile glibc 2.39's double-precision math routines the way glibc's x86-64 multiarch build compiles
# their FMA variants (-O2 -frounding-math -mfma -mavx2; log10 and branred have no FMA variant) and dump
# GCC's optimized GIMPLE (with vectorization off) for gim2x.py. The objects are only compiled, never run:
# extract_tables.py reads their constant tables, and the translations are checked against the system libm
# (test/run_tests.sh).
set -e
GL=$(readlink -f "$1"); OUT=$(readlink -f "$2"); HERE=$(cd "$(dirname "$0")" && pwd)
G=$GL/sysdeps/ieee754/dbl-64
mkdir -p "$OUT/gimple"; cd "$OUT"
cp -r "$HERE/stub" . ; cp "$HERE/mathstubs.c" .
CF="-O2 -fno-tree-vectorize -fno-tree-slp-vectorize -frounding-math -std=gnu11 -include $OUT/stub/shim.h -include $OUT/stub/mylog.h -I$OUT/stub -I$G"
FMA="-mfma -mavx2"
build() { # name extra-flags...
  local f=$1; shift
  gcc $CF "$@" -c -o $f.o $G/$f.c
  (cd gimple && gcc $CF "$@" -fdump-tree-optimized -c -o /dev/null $G/$f.c)
}
build e_exp $FMA -D__exp=my_exp_fma
build e_log $FMA -D__log=my_log
build e_pow $FMA -D__pow=my_pow
build s_sin $FMA -D__sin=my_sin -D__cos=my_cos
build s_tan $FMA -D__tan=my_tan
build e_asin $FMA -D__ieee754_asin=my_asin -D__ieee754_acos=my_acos
build s_atan $FMA -D__atan=my_atan
build e_log10 -D__ieee754_log10=my_log10 -D__ieee754_log=my_log
for d in e_exp_data e_log_data e_pow_log_data sincostab branred; do gcc $CF -c -o $d.o $G/$d.c; done
gcc -O2 -c -o mathstubs.o mathstubs.c
# (no glibc-derived code is run here: the translated routines are checked against the system libm by the TS/Java test harness)


