#!/bin/bash
# make_libm.sh GLIBC_SRC [WORKDIR]
# Regenerate the TypeScript, Java and Go ports of glibc 2.39's math routines from glibc's source:
#   1. build_glibm.sh   compile the routines as glibc's x86-64 FMA build does, dump GCC's optimized GIMPLE
#   2. extract_tables.py copy the constant tables out of the object files (objdump/objcopy; nothing is run)
#   3. gim2x.py         translate the GIMPLE to ts/src/glibm.ts, java/src/swmm5/Glibm.java and go/glibm/glibm.go
# GLIBC_SRC is a checkout of glibc 2.39; the one used was release/2.39/master at ce65d944e38a. Written by Claude (Anthropic); see AI-USE.md.
set -e
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
GL=$(readlink -f "$1"); W=${2:-$ROOT/build/glibm}; mkdir -p "$W"
"$HERE/build_glibm.sh" "$GL" "$W"
python3 "$HERE/extract_tables.py" "$W" "$W/tables.json"
G=$W/gimple
DUMPS="$G/e_exp.c.254t.optimized $G/e_log.c.254t.optimized $G/e_pow.c.254t.optimized $G/s_sin.c.254t.optimized
       $G/s_tan.c.254t.optimized $G/e_asin.c.254t.optimized $G/s_atan.c.254t.optimized $G/e_log10.c.254t.optimized"
NAMES="--name my_exp_fma=exp --name my_log=log --name my_sin=sin --name my_cos=cos
       --name my_tan=tan --name my_asin=ieee754_asin --name my_acos=ieee754_acos --name my_atan=atan
       --name my_log10=ieee754_log10"
# TypeScript calls the translation pow_exact and puts a hand-written fast path, pow, in front of it;
# Java calls the translation pow directly
python3 "$HERE/gim2x.py" ts "$ROOT/ts/src/glibm.ts" $DUMPS --tables "$W/tables.json" $NAMES --name my_pow=pow_exact
python3 "$HERE/gim2x.py" java "$ROOT/java/src/swmm5/Glibm.java" $DUMPS --tables "$W/tables.json" $NAMES --name my_pow=pow
# Go exports capitalised names; log10, asin and acos are wrapped by hand-written SVID wrappers (Log10, Asin, Acos)
python3 "$HERE/gim2x.py" go "$ROOT/go/glibm/glibm.go" $DUMPS --tables "$W/tables.json" --name my_exp_fma=Exp --name my_log=Log \
    --name my_pow=Pow --name my_sin=Sin --name my_cos=Cos --name my_tan=Tan --name my_asin=ieee754_asin \
    --name my_acos=ieee754_acos --name my_atan=Atan --name my_log10=ieee754_log10
cp "$HERE/glibm_rt.ts" "$ROOT/ts/src/glibm_rt.ts"
cp "$HERE/GlibmRt.java" "$ROOT/java/src/swmm5/GlibmRt.java"
cp "$HERE/glibm_rt.go" "$ROOT/go/glibm/glibm_rt.go"
gofmt -w "$ROOT/go/glibm"
echo "wrote ts/src/glibm.ts, java/src/swmm5/Glibm.java and go/glibm/glibm.go; now run tools/libm/test/run_tests.sh"
