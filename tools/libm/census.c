/* census.c - LD_PRELOAD interposer: counts calls to the libm functions EPA SWMM imports and keeps a
   reservoir sample of their arguments, written at exit to $LIBM_CENSUS (binary: name\0 count:u64 n:u32
   then n records of 1 or 2 doubles). Used to build test sets for the TypeScript/Java libm ports.
   Written by Claude (Anthropic). */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#define CAP 20000
typedef struct { const char *name; int nargs; uint64_t count; uint32_t n; double (*a)[2]; void *real; } Fn;
static Fn fns[] = {{"exp",1},{"log",1},{"log10",1},{"pow",2},{"sin",1},{"cos",1},{"tan",1},
                   {"asin",1},{"acos",1},{"atan",1},{"sqrt",1}};
enum {F_EXP, F_LOG, F_LOG10, F_POW, F_SIN, F_COS, F_TAN, F_ASIN, F_ACOS, F_ATAN, F_SQRT, NF};
static uint64_t rng = 88172645463325252ull;
static uint64_t xr(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
static void rec(int i, double x, double y) {
  Fn *f = &fns[i]; f->count++;
  if (!f->a) f->a = malloc(sizeof(double[2]) * CAP);
  uint64_t k = f->n < CAP ? f->n++ : xr() % f->count;
  if (k < CAP) { f->a[k][0] = x; f->a[k][1] = y; }
}
static void *real(int i) {
  if (!fns[i].real) {
    const char *ver = (i == F_EXP || i == F_LOG || i == F_POW) ? "GLIBC_2.29" : "GLIBC_2.2.5";
    fns[i].real = dlvsym(RTLD_NEXT, fns[i].name, ver);
  }
  return fns[i].real;
}
#define F1(nm, I) double nm(double x) { rec(I, x, 0); return ((double(*)(double))real(I))(x); }
F1(exp, F_EXP) F1(log, F_LOG) F1(log10, F_LOG10) F1(sin, F_SIN) F1(cos, F_COS) F1(tan, F_TAN)
F1(asin, F_ASIN) F1(acos, F_ACOS) F1(atan, F_ATAN) F1(sqrt, F_SQRT)
double pow(double x, double y) { rec(F_POW, x, y); return ((double(*)(double, double))real(F_POW))(x, y); }
__attribute__((destructor)) static void dump(void) {
  const char *p = getenv("LIBM_CENSUS"); if (!p) return;
  FILE *o = fopen(p, "wb"); if (!o) return;
  for (int i = 0; i < NF; i++) {
    Fn *f = &fns[i]; fwrite(f->name, 1, strlen(f->name) + 1, o);
    fwrite(&f->count, 8, 1, o); fwrite(&f->n, 4, 1, o);
    for (uint32_t j = 0; j < f->n; j++) fwrite(f->a[j], 8, f->nargs, o);
  }
  fclose(o);
}
