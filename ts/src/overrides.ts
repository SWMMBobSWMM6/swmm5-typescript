// overrides.ts - hand-ported pieces of EPA SWMM 5.2.4 that use C pointer arithmetic the translator does
// not map: odesolve.c (whole file), mempool.c (its pool becomes plain byte buffers) and six helpers from
// swmm5.c and project.c. Each keeps the C statement order and floating-point expressions exactly; pow is
// glibc's. Written by Claude (Anthropic); see AI-USE.md.
import * as R from "./crt";
import { CPtr } from "./crt";
import * as M from "./glibm";

const MAXFNAME = 259;

export function sstrncpy(dest: CPtr, src: CPtr, n: number): number {
  let offset = 0;
  if (n > 0) {
    while (src.g(offset) !== 0) {
      if (offset === n) break;
      dest.s(offset, src.g(offset));
      offset++;
    }
  }
  dest.s(offset, 0);
  return R.strlen(dest);
}

export function sstrcat(dest: CPtr, src: CPtr, size: number): number {
  let offset = R.strlen(dest);
  let srcIndex = 0;
  while (src.g(srcIndex) !== 0) {
    dest.s(offset, src.g(srcIndex));
    offset++;
    srcIndex++;
    if (offset === size - 1) break;
  }
  dest.s(offset, 0);
  return R.strlen(dest);
}

function isRelativePath(fname: CPtr): boolean {
  if (R.strchr(fname, 58) !== null) return false;
  if (fname.g(0) === 92) return false;
  if (fname.g(0) === 47) return false;
  return true;
}

export function getAbsolutePath(fname: CPtr, absPath: CPtr, size: number): void {
  if (fname === null || R.strlen(fname) === 0) return;
  if (isRelativePath(fname)) {
    const full = R.realpathOf(R.strOf(fname));       // realpath(fname, absPath)
    const m = Math.min(full.length, absPath.b.length - absPath.o - 1);
    for (let i = 0; i < m; i++) absPath.s(i, full.charCodeAt(i));
    absPath.s(m, 0);
  } else {
    sstrncpy(absPath, fname, R.strlen(fname));
  }
  const endOfDir = R.strrchr(absPath, 47);
  if (endOfDir !== null) endOfDir.s(1, 0);
}

export function getTempFileName(fname: CPtr): CPtr {
  sstrncpy(fname, R.lit("swmmXXXXXX"), MAXFNAME);
  R.mkstemp(fname);
  return fname;
}

export function project_createMatrix(nrows: number, ncols: number): Float64Array[] {
  if (nrows <= 0 || ncols <= 0) return null;
  return Array.from({ length: nrows }, () => new Float64Array(ncols));
}

export function project_freeMatrix(a: any): void {}

// ------------------------------------------------------------------ mempool.c: the pool hands out byte buffers
export function AllocInit(): any { return {}; }
export function Alloc(n: number): CPtr { return R.buf(n); }
export function AllocSetPool(p: any): any { return p; }
export function AllocReset(): void {}
export function AllocFreePool(): void {}

// ------------------------------------------------------------------ odesolve.c (Cash-Karp Runge-Kutta)
const MAXSTP = 10000;
const TINY = 1.0e-30;
const SAFETY = 0.9;
const PGROW = -0.2;
const PSHRNK = -0.25;
const ERRCON = 1.89e-4;
let nmax = 0;
let y: Float64Array, yscal: Float64Array, dydx: Float64Array, yerr: Float64Array, ytemp: Float64Array;
let ak2: Float64Array, ak3: Float64Array, ak4: Float64Array, ak5: Float64Array, ak6: Float64Array;
type Derivs = (x: number, y: Float64Array, dydx: Float64Array) => void;

export function odesolve_open(n: number): number {
  nmax = 0;
  y = new Float64Array(n); yscal = new Float64Array(n); dydx = new Float64Array(n);
  yerr = new Float64Array(n); ytemp = new Float64Array(n);
  ak2 = new Float64Array(n); ak3 = new Float64Array(n); ak4 = new Float64Array(n);
  ak5 = new Float64Array(n); ak6 = new Float64Array(n);
  nmax = n;
  return 1;
}

export function odesolve_close(): void { nmax = 0; }

export function odesolve_integrate(ystart: Float64Array, n: number, x1: number, x2: number, eps: number, h1: number,
                                   derivs: Derivs): number {
  const hdid = new Float64Array(1), hnext = new Float64Array(1), x = new Float64Array(1);
  x[0] = x1;
  let h = h1;
  if (nmax < n) return 1;
  for (let i = 0; i < n; i++) y[i] = ystart[i];
  for (let nstp = 1; nstp <= MAXSTP; nstp++) {
    derivs(x[0], y, dydx);
    for (let i = 0; i < n; i++) yscal[i] = Math.abs(y[i]) + Math.abs(dydx[i] * h) + TINY;
    if ((x[0] + h - x2) * (x[0] + h - x1) > 0.0) h = x2 - x[0];
    const errcode = rkqs(x, n, h, eps, hdid, hnext, derivs);
    if (errcode) break;
    if ((x[0] - x2) * (x2 - x1) >= 0.0) {
      for (let i = 0; i < n; i++) ystart[i] = y[i];
      return 0;
    }
    if (Math.abs(hnext[0]) <= 0.0) return 2;
    h = hnext[0];
  }
  return 3;
}

function rkqs(x: Float64Array, n: number, htry: number, eps: number, hdid: Float64Array, hnext: Float64Array,
              derivs: Derivs): number {
  const xold = x[0];
  let h = htry;
  for (;;) {
    rkck(xold, n, h, derivs);
    let errmax = 0.0;
    for (let i = 0; i < n; i++) {
      const err = Math.abs(yerr[i] / yscal[i]);
      if (err > errmax) errmax = err;
    }
    errmax /= eps;
    if (errmax > 1.0) {
      const htemp = SAFETY * h * M.pow(errmax, PSHRNK);
      if (h >= 0) h = htemp > 0.1 * h ? htemp : 0.1 * h;
      else h = htemp < 0.1 * h ? htemp : 0.1 * h;
      const xnew = xold + h;
      if (xnew === xold) return 2;
      continue;
    } else {
      if (errmax > ERRCON) hnext[0] = SAFETY * h * M.pow(errmax, PGROW);
      else hnext[0] = 5.0 * h;
      hdid[0] = h;
      x[0] += h;
      for (let i = 0; i < n; i++) y[i] = ytemp[i];
      return 0;
    }
  }
}

function rkck(x: number, n: number, h: number, derivs: Derivs): void {
  const a2 = 0.2, a3 = 0.3, a4 = 0.6, a5 = 1.0, a6 = 0.875;
  const b21 = 0.2, b31 = 3.0 / 40.0, b32 = 9.0 / 40.0, b41 = 0.3, b42 = -0.9, b43 = 1.2;
  const b51 = -11.0 / 54.0, b52 = 2.5, b53 = -70.0 / 27.0, b54 = 35.0 / 27.0;
  const b61 = 1631.0 / 55296.0, b62 = 175.0 / 512.0, b63 = 575.0 / 13824.0;
  const b64 = 44275.0 / 110592.0, b65 = 253.0 / 4096.0, c1 = 37.0 / 378.0;
  const c3 = 250.0 / 621.0, c4 = 125.0 / 594.0, c6 = 512.0 / 1771.0;
  const dc5 = -277.0 / 14336.0;
  const dc1 = c1 - 2825.0 / 27648.0, dc3 = c3 - 18575.0 / 48384.0;
  const dc4 = c4 - 13525.0 / 55296.0, dc6 = c6 - 0.25;
  for (let i = 0; i < n; i++) ytemp[i] = y[i] + b21 * h * dydx[i];
  derivs(x + a2 * h, ytemp, ak2);
  for (let i = 0; i < n; i++) ytemp[i] = y[i] + h * (b31 * dydx[i] + b32 * ak2[i]);
  derivs(x + a3 * h, ytemp, ak3);
  for (let i = 0; i < n; i++) ytemp[i] = y[i] + h * (b41 * dydx[i] + b42 * ak2[i] + b43 * ak3[i]);
  derivs(x + a4 * h, ytemp, ak4);
  for (let i = 0; i < n; i++) ytemp[i] = y[i] + h * (b51 * dydx[i] + b52 * ak2[i] + b53 * ak3[i] + b54 * ak4[i]);
  derivs(x + a5 * h, ytemp, ak5);
  for (let i = 0; i < n; i++) ytemp[i] = y[i] + h * (b61 * dydx[i] + b62 * ak2[i] + b63 * ak3[i] + b64 * ak4[i] + b65 * ak5[i]);
  derivs(x + a6 * h, ytemp, ak6);
  for (let i = 0; i < n; i++) ytemp[i] = y[i] + h * (c1 * dydx[i] + c3 * ak3[i] + c4 * ak4[i] + c6 * ak6[i]);
  for (let i = 0; i < n; i++) yerr[i] = h * (dc1 * dydx[i] + dc3 * ak3[i] + dc4 * ak4[i] + dc5 * ak5[i] + dc6 * ak6[i]);
}
