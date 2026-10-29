// crt.ts - C runtime layer for the TypeScript port of EPA SWMM 5.2.4 (Node.js).
//
// Reproduces the glibc and x86-64 behaviour SWMM's output depends on: printf formatting with glibc's
// rounding (round half to even on the exact binary value), strtod/sscanf parsing, NUL-terminated byte
// strings, stdio files with C's positioning and end-of-file rules, and x86's float-to-integer
// conversion. The math functions are in glibm.ts (bit-exact ports of glibc 2.39's FMA variants).
// Written by Claude (Anthropic); see AI-USE.md.
import * as fs from "fs";
import * as path from "path";

// ------------------------------------------------------------------ char* : bytes plus an offset
export class CPtr {
  b: Uint8Array;
  o: number;
  constructor(b: Uint8Array, o: number) { this.b = b; this.o = o; }
  g(i: number): number { return (this.b[this.o + i] << 24) >> 24; }      // signed char, as x86 C
  s(i: number, v: number): number { this.b[this.o + i] = v; return v; }
  add(n: number): CPtr { return new CPtr(this.b, this.o + n); }
}

export function lit(s: string): CPtr {
  const b = new Uint8Array(s.length + 1);
  for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
  return new CPtr(b, 0);
}
export function buf(n: number): CPtr { return new CPtr(new Uint8Array(Math.max(n, 1)), 0); }
export function bufFrom(src: CPtr, n: number): CPtr {
  const p = buf(n);
  const m = Math.min(strlen(src), n);
  p.b.set(src.b.subarray(src.o, src.o + m), 0);
  return p;
}
export function bufOf(n: number, vals: number[]): CPtr {
  const p = buf(n);
  for (let i = 0; i < vals.length; i++) p.b[i] = vals[i];
  return p;
}
export function bufs(n: number, m: number): CPtr[] { return Array.from({ length: n }, () => buf(m)); }
export function copyBuf(p: CPtr): CPtr { return new CPtr(p.b.slice(), p.o); }
export function reallocBuf(p: CPtr, n: number): CPtr {
  const q = buf(n);
  if (p !== null) q.b.set(p.b.subarray(p.o, p.o + Math.min(n, p.b.length - p.o)), 0);
  return q;
}
export function peq(a: CPtr, b: CPtr): boolean {
  return a === b || (a !== null && b !== null && a.b === b.b && a.o === b.o);
}
export function bytesOf(p: CPtr): Uint8Array {
  let e = p.o;
  const b = p.b;
  while (e < b.length && b[e] !== 0) e++;
  return b.subarray(p.o, e);
}
export function strOf(p: CPtr): string {
  const s = bytesOf(p);
  let r = "";
  for (let i = 0; i < s.length; i++) r += String.fromCharCode(s[i]);
  return r;
}

// ------------------------------------------------------------------ references (C's T* to one object)
export interface Ref { v: any; }
export class Box implements Ref {
  v: any;
  constructor(v: any) { this.v = v; }
}
export class ElemRef implements Ref {
  a: any; i: number;
  constructor(a: any, i: number) { this.a = a; this.i = i; }
  get v(): any { return this.a[this.i]; }
  set v(x: any) { this.a[this.i] = x; }
}
export class FieldRef implements Ref {
  o: any; k: string;
  constructor(o: any, k: string) { this.o = o; this.k = k; }
  get v(): any { return this.o[this.k]; }
  set v(x: any) { this.o[this.k] = x; }
}
export class CharRef implements Ref {
  p: CPtr; i: number;
  constructor(p: CPtr, i: number) { this.p = p; this.i = i; }
  get v(): number { return this.p.g(this.i); }
  set v(x: number) { this.p.s(this.i, x); }
}
export function acc(get: () => any, set: (t: any) => void): Ref {
  return { get v() { return get(); }, set v(t: any) { set(t); } };
}

// ------------------------------------------------------------------ arithmetic with C semantics
export function D2I(x: number): number {        // (int)x on x86-64: NaN or out of range gives INT_MIN
  if (!(x > -2147483649 && x < 2147483648)) return -2147483648;
  return x | 0;
}
export function D2L(x: number): number {        // (long)x; a long is a JavaScript number (SWMM's stay < 2^53)
  if (!(x > -9223372036854775809 && x < 9223372036854775808)) return -9223372036854775808;
  return Math.trunc(x) + 0;
}
export function toUL(x: number): number { return x < 0 ? x + 18446744073709551616 : x; }
export function postf(r: Ref, d: number): number { const o = r.v; r.v = Math.fround(o + d); return o; }
export function postu(r: Ref, d: number): number { const o = r.v; r.v = (o + d) >>> 0; return o; }
export function arr1(r: Ref, T: any, f: (a: any) => any): any {
  const a = new T(1); a[0] = r.v;
  const res = f(a);
  r.v = a[0];
  return res;
}
export function chr1(r: Ref, f: (p: CPtr) => any): any {
  const p = buf(2); p.s(0, r.v);
  const res = f(p);
  r.v = p.g(0);
  return res;
}

// ------------------------------------------------------------------ memory
export function recs<T>(n: number, mk: () => T): T[] { return Array.from({ length: n }, mk); }
export function nulls(n: number): any[] { return new Array(n).fill(null); }
export function grid<T>(n: number, mk: () => T): T[] { return Array.from({ length: n }, mk); }
export function cloneGrid(g: any[]): any[] { return g.map((x) => (x instanceof CPtr ? copyBuf(x) : x.slice())); }
export function reallocArr(a: any[], n: number, mk: () => any): any[] {
  if (a === null) return Array.from({ length: n }, mk);
  if (n <= a.length) return a.slice(0, n);
  const r = a.slice();
  while (r.length < n) r.push(mk());
  return r;
}
export function reallocTyped(a: any, n: number, T: any): any {
  const r = new T(n);
  if (a !== null) r.set(a.subarray(0, Math.min(n, a.length)), 0);
  return r;
}
export function memset(a: any, v: number, n: number): void {
  if (a instanceof CPtr) { a.b.fill(v & 255, a.o, a.o + n); return; }
  a.fill(0, 0, Math.min(a.length, Math.trunc(n / a.BYTES_PER_ELEMENT)));
}

// ------------------------------------------------------------------ strings
export function strlen(p: CPtr): number {
  const b = p.b; let e = p.o;
  while (b[e] !== 0) e++;
  return e - p.o;
}
export function strcpy(d: CPtr, s: CPtr): CPtr {
  const n = strlen(s);
  if (d.b === s.b) d.b.copyWithin(d.o, s.o, s.o + n + 1);
  else d.b.set(s.b.subarray(s.o, s.o + n + 1), d.o);
  return d;
}
export function strcat(d: CPtr, s: CPtr): CPtr { strcpy(d.add(strlen(d)), s); return d; }
export function strcmp(a: CPtr, b: CPtr): number {
  let i = a.o, j = b.o;
  const x = a.b, y = b.b;
  for (;;) {
    const c = x[i], d = y[j];
    if (c !== d) return c - d;
    if (c === 0) return 0;
    i++; j++;
  }
}
export function strchr(s: CPtr, c: number): CPtr {
  c &= 255;
  const b = s.b;
  for (let i = s.o; ; i++) {
    if (b[i] === c) return new CPtr(b, i);
    if (b[i] === 0) return null;
  }
}
export function strrchr(s: CPtr, c: number): CPtr {
  c &= 255;
  const e = s.o + strlen(s);
  if (c === 0) return new CPtr(s.b, e);
  for (let i = e - 1; i >= s.o; i--) if (s.b[i] === c) return new CPtr(s.b, i);
  return null;
}
export function strstr(h: CPtr, n: CPtr): CPtr {
  const hb = bytesOf(h), nb = bytesOf(n);
  outer: for (let i = 0; i + nb.length <= hb.length; i++) {
    for (let k = 0; k < nb.length; k++) if (hb[i + k] !== nb[k]) continue outer;
    return new CPtr(h.b, h.o + i);
  }
  return null;
}
export function strcspn(s: CPtr, rej: CPtr): number {
  const sb = bytesOf(s), rb = bytesOf(rej);
  for (let i = 0; i < sb.length; i++) if (rb.indexOf(sb[i]) >= 0) return i;
  return sb.length;
}
let tokNext: CPtr = null;
export function strtok(s: CPtr, delim: CPtr): CPtr {
  if (s !== null) tokNext = s;
  const p = tokNext;
  if (p === null) return null;
  const ds = bytesOf(delim);
  const b = p.b;
  let i = p.o;
  while (b[i] !== 0 && ds.indexOf(b[i]) >= 0) i++;
  if (b[i] === 0) { tokNext = null; return null; }
  const start = i;
  while (b[i] !== 0 && ds.indexOf(b[i]) < 0) i++;
  if (b[i] !== 0) { b[i] = 0; tokNext = new CPtr(b, i + 1); }
  else tokNext = null;
  return new CPtr(b, start);
}
export function toupper(c: number): number { return c >= 97 && c <= 122 ? c - 32 : c; }

function isSpace(c: number): boolean { return c === 32 || (c >= 9 && c <= 13); }
function low(c: number): number { return c >= 65 && c <= 90 ? c | 0x20 : c; }
const DEFAULT_NAN = (() => { const f = new Float64Array(1); new Uint32Array(f.buffer)[1] = 0xfff80000; return f[0]; })();
const POS_NAN = (() => { const f = new Float64Array(1); new Uint32Array(f.buffer)[1] = 0x7ff80000; return f[0]; })();

// parse a C floating-point number at b[i..] (at most maxw characters); [value, end] or [0, -1]
function scanDouble(b: Uint8Array, i: number, maxw: number, skipSpace: boolean): [number, number] {
  const n = b.length;
  if (skipSpace) while (i < n && isSpace(b[i])) i++;
  const lim = maxw < 0 ? n : i + maxw;
  const g = (k: number) => (k < lim && k < n ? b[k] : 0);
  let neg = false;
  if (g(i) === 43 || g(i) === 45) { neg = g(i) === 45; i++; }
  if (low(g(i)) === 105 && low(g(i + 1)) === 110 && low(g(i + 2)) === 102) {
    i += 3;
    if (low(g(i)) === 105 && low(g(i + 1)) === 110 && low(g(i + 2)) === 105 && low(g(i + 3)) === 116 && low(g(i + 4)) === 121) i += 5;
    return [neg ? -Infinity : Infinity, i];
  }
  if (low(g(i)) === 110 && low(g(i + 1)) === 97 && low(g(i + 2)) === 110) {
    i += 3;
    if (g(i) === 40) {
      let k = i + 1;
      while ((g(k) >= 48 && g(k) <= 57) || (low(g(k)) >= 97 && low(g(k)) <= 122) || g(k) === 95) k++;
      if (g(k) === 41) i = k + 1;
    }
    return [neg ? DEFAULT_NAN : POS_NAN, i];
  }
  const ms = i;
  let nd = 0;
  while (g(i) >= 48 && g(i) <= 57) { i++; nd++; }
  if (g(i) === 46) { i++; while (g(i) >= 48 && g(i) <= 57) { i++; nd++; } }
  if (nd === 0) return [0, -1];
  if (low(g(i)) === 101) {
    let k = i + 1;
    if (g(k) === 43 || g(k) === 45) k++;
    if (g(k) >= 48 && g(k) <= 57) { while (g(k) >= 48 && g(k) <= 57) k++; i = k; }
  }
  let s = "";
  for (let k = ms; k < i; k++) s += String.fromCharCode(b[k]);
  const v = Number(s);                    // correctly rounded, as glibc's strtod
  return [neg ? -v : v, i];
}
export function strtod(s: CPtr, end: Ref): number {
  const [v, e] = scanDouble(s.b, s.o, -1, true);
  if (end !== null) end.v = e < 0 ? s : new CPtr(s.b, e);
  return e < 0 ? 0 : v;
}
export function atof(s: CPtr): number { return strtod(s, null); }
export function atoi(s: CPtr): number {
  const b = s.b;
  let i = s.o;
  while (b[i] !== 0 && isSpace(b[i])) i++;
  let neg = false;
  if (b[i] === 43 || b[i] === 45) { neg = b[i] === 45; i++; }
  let v = 0;
  while (b[i] >= 48 && b[i] <= 57) { v = v * 10 + b[i] - 48; i++; if (v > 9.3e18) v = 9.3e18; }
  if (neg) v = -v;
  if (v > 9223372036854775807) v = 9223372036854775807;
  if (v < -9223372036854775808) v = -9223372036854775808;
  return Number(BigInt.asIntN(32, BigInt(v)));     // strtol's long, then (int): keeps the low 32 bits
}

// ------------------------------------------------------------------ sscanf
// arguments: [tag, ref] pairs for numbers ('i' int, 'l' long, 'd' double, 'f' float), CPtr for %s and %c
export function sscanf(s: CPtr, fmt: CPtr, ...args: any[]): number {
  const b = s.b;
  let i = s.o;
  const f = bytesOf(fmt);
  let k = 0, ai = 0, count = 0, anyInput = false;
  const n = b.length, nf = f.length;
  const ch = (j: number) => (j < n ? b[j] : 0);
  while (k < nf) {
    const fc = f[k];
    if (isSpace(fc)) {
      while (k < nf && isSpace(f[k])) k++;
      while (ch(i) !== 0 && isSpace(ch(i))) i++;
      continue;
    }
    if (fc !== 37) {
      if (ch(i) === 0) return anyInput || count > 0 ? count : -1;
      if (ch(i) !== fc) return count;
      i++; k++;
      continue;
    }
    k++;
    if (f[k] === 37) {
      while (ch(i) !== 0 && isSpace(ch(i))) i++;
      if (ch(i) !== 37) return count;
      i++; k++;
      continue;
    }
    let suppress = false;
    if (f[k] === 42) { suppress = true; k++; }
    let width = 0;
    while (f[k] >= 48 && f[k] <= 57) { width = width * 10 + f[k] - 48; k++; }
    let lmod = 0;
    while (f[k] === 108 || f[k] === 104 || f[k] === 76) { if (f[k] === 108) lmod++; k++; }
    const conv = f[k];
    k++;
    if (conv !== 99 && conv !== 91 && conv !== 110) while (ch(i) !== 0 && isSpace(ch(i))) i++;
    if (ch(i) === 0 && conv !== 110) return count === 0 && !anyInput ? -1 : count;
    if (conv === 100 || conv === 105 || conv === 117) {            // d i u
      let j = i;
      const lim = width ? i + width : n;
      let neg = false;
      if (j < lim && (ch(j) === 43 || ch(j) === 45)) { neg = ch(j) === 45; j++; }
      const ds = j;
      let v = 0;
      while (j < lim && ch(j) >= 48 && ch(j) <= 57) { v = v * 10 + ch(j) - 48; j++; if (v > 9.3e18) v = 9.3e18; }
      if (j === ds) return count;
      i = j;
      anyInput = true;
      if (neg) v = -v;
      if (!suppress) {
        const a = args[ai++];
        if (v > 9223372036854775807) v = 9223372036854775807;
        if (v < -9223372036854775808) v = -9223372036854775808;
        a[1].v = lmod > 0 || a[0] === "l" ? v : Number(BigInt.asIntN(32, BigInt(v)));
        count++;
      }
    } else if (conv === 102 || conv === 101 || conv === 103) {      // f e g
      const [v, e] = scanDouble(b, i, width ? width : -1, false);
      if (e < 0) return count;
      i = e;
      anyInput = true;
      if (!suppress) {
        const a = args[ai++];
        a[1].v = lmod > 0 || a[0] === "d" ? v : Math.fround(v);
        count++;
      }
    } else if (conv === 115) {                                       // s
      let j = i;
      const w = width ? width : 1 << 30;
      let m = 0;
      while (ch(j) !== 0 && !isSpace(ch(j)) && m < w) { j++; m++; }
      if (!suppress) {
        const d: CPtr = args[ai++];
        d.b.set(b.subarray(i, j), d.o);
        d.b[d.o + m] = 0;
        count++;
      }
      i = j;
      anyInput = true;
    } else if (conv === 99) {                                        // c
      const cnt = width ? width : 1;
      if (ch(i) === 0) return count === 0 && !anyInput ? -1 : count;
      const a = suppress ? null : args[ai++];
      let t = 0;
      while (t < cnt && ch(i) !== 0) {
        if (!suppress) {
          if (a instanceof CPtr) a.b[a.o + t] = ch(i);
          else { const c = ch(i); a[1].v = c > 127 ? c - 256 : c; }
        }
        i++; t++;
      }
      anyInput = true;
      if (!suppress) count++;
    } else {
      throw new Error("sscanf %" + String.fromCharCode(conv));
    }
  }
  return count;
}

// ------------------------------------------------------------------ printf
const P2 = new Float64Array(64);
for (let k = 0; k < 64; k++) P2[k] = 2 ** k;

function decompose(a: number): [bigint, number] {         // a = m * 2^e, a finite and >= 0
  const f = new Float64Array([a]);
  const u = new Uint32Array(f.buffer);
  const hi = u[1], lo = u[0];
  const ex = (hi >>> 20) & 0x7ff;
  let m = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  if (ex === 0) return [m, -1074];
  m |= 1n << 52n;
  return [m, ex - 1075];
}
function scaledRound(a: number, p: number): bigint {      // round_half_even(a * 10^p), exactly
  const [m, e] = decompose(a);
  let num = m, den = 1n;
  if (e >= 0) num <<= BigInt(e); else den <<= BigInt(-e);
  if (p >= 0) num *= 10n ** BigInt(p); else den *= 10n ** BigInt(-p);
  let q = num / den;
  const r = num - q * den;
  const twice = r * 2n;
  if (twice > den || (twice === den && (q & 1n) === 1n)) q += 1n;
  return q;
}
function fixedDigits(a: number, prec: number, alt: boolean): string {
  let s: string;
  if (a < 1e21 && prec <= 100) {
    s = a.toFixed(prec);                                  // exact, but rounds ties away from zero
    if (prec + 1 < 64) {
      const t = a * P2[prec + 1];                         // a tie iff a * 2^(prec+1) is an odd integer
      if (Number.isInteger(t) && t % 2 === 1) {
        const last = s.charCodeAt(s.length - 1) - 48;
        if (last % 2 === 1) s = s.slice(0, -1) + String.fromCharCode(48 + last - 1);
      }
    }
  } else {
    const d = scaledRound(a, prec).toString();
    s = prec === 0 ? d : (d.length <= prec ? "0".repeat(prec - d.length + 1) + d : d);
    if (prec > 0) s = s.slice(0, s.length - prec) + "." + s.slice(s.length - prec);
  }
  if (prec === 0 && alt) s += ".";
  return s;
}
function expDigitsRaw(a: number, prec: number): [string, number] {
  if (a === 0) return ["0".repeat(prec + 1), 0];
  let E = Math.floor(Math.log10(a));
  for (let iter = 0; iter < 4; iter++) {
    const d = scaledRound(a, prec - E).toString();
    if (d.length === prec + 1) return [d, E];
    if (d.length > prec + 1) E++; else E--;
  }
  return [scaledRound(a, prec - E).toString(), E];
}
function expDigits(a: number, prec: number, alt: boolean, upper: boolean): string {
  const [d, X] = expDigitsRaw(a, prec);
  const mant = d.slice(0, 1) + (prec > 0 ? "." + d.slice(1) : alt ? "." : "");
  const ax = Math.abs(X);
  return mant + (upper ? "E" : "e") + (X < 0 ? "-" : "+") + (ax < 10 ? "0" + ax : String(ax));
}
function formatFloat(a: number, conv: number, prec: number, alt: boolean): string {
  const lc = conv | 0x20;
  if (lc === 102) return fixedDigits(a, prec, alt);
  if (lc === 101) return expDigits(a, prec, alt, conv === 69);
  const P = prec === 0 ? 1 : prec;
  const X = a === 0 ? 0 : expDigitsRaw(a, P - 1)[1];
  let s = P > X && X >= -4 ? fixedDigits(a, P - 1 - X, alt) : expDigits(a, P - 1, alt, conv === 71);
  if (!alt) {
    const ep = s.search(/[eE]/);
    let mant = ep >= 0 ? s.slice(0, ep) : s;
    const ex = ep >= 0 ? s.slice(ep) : "";
    if (mant.indexOf(".") >= 0) { mant = mant.replace(/0+$/, ""); if (mant.endsWith(".")) mant = mant.slice(0, -1); }
    s = mant + ex;
  }
  return s;
}
const F1 = new Float64Array(1), U1 = new Uint32Array(F1.buffer);
function signBit(x: number): boolean { F1[0] = x; return (U1[1] >>> 31) === 1; }

// the formatted bytes as a latin-1 string (one char per byte)
export function format(fmt: CPtr, args: any[]): string {
  const f = fmt.b;
  let k = fmt.o, ai = 0;
  let o = "";
  while (f[k] !== 0) {
    if (f[k] !== 37) {
      let e = k;
      while (f[e] !== 0 && f[e] !== 37) e++;
      for (let q = k; q < e; q++) o += String.fromCharCode(f[q]);
      k = e;
      continue;
    }
    k++;
    if (f[k] === 37) { o += "%"; k++; continue; }
    let left = false, plus = false, space = false, alt = false, zero = false;
    for (;; k++) {
      const c = f[k];
      if (c === 45) left = true;
      else if (c === 43) plus = true;
      else if (c === 32) space = true;
      else if (c === 35) alt = true;
      else if (c === 48) zero = true;
      else break;
    }
    let width = 0, prec = -1;
    if (f[k] === 42) { width = args[ai++]; if (width < 0) { left = true; width = -width; } k++; }
    else while (f[k] >= 48 && f[k] <= 57) { width = width * 10 + f[k] - 48; k++; }
    if (f[k] === 46) {
      k++; prec = 0;
      if (f[k] === 42) { prec = args[ai++]; if (prec < 0) prec = -1; k++; }
      else while (f[k] >= 48 && f[k] <= 57) { prec = prec * 10 + f[k] - 48; k++; }
    }
    while (f[k] === 108 || f[k] === 104 || f[k] === 76 || f[k] === 122 || f[k] === 106 || f[k] === 116 || f[k] === 113) k++;
    const conv = f[k];
    k++;
    let body: string, sign = "", numeric = true;
    switch (conv) {
      case 100: case 105: {                                   // d i
        const v = Math.trunc(args[ai++]);
        if (v < 0) sign = "-"; else if (plus) sign = "+"; else if (space) sign = " ";
        body = String(Math.abs(v));
        if (prec >= 0) { zero = false; if (prec === 0 && v === 0) body = ""; body = body.padStart(prec, "0"); }
        break;
      }
      case 117: case 120: case 88: case 111: {                // u x X o
        let v = Math.trunc(args[ai++]);
        if (v < 0) v = v >= -2147483648 ? v + 4294967296 : v + 18446744073709551616;
        body = conv === 117 ? String(v) : conv === 111 ? v.toString(8) : conv === 120 ? v.toString(16) : v.toString(16).toUpperCase();
        if (prec >= 0) { zero = false; if (prec === 0 && v === 0) body = ""; body = body.padStart(prec, "0"); }
        break;
      }
      case 99: {                                              // c
        numeric = false;
        body = String.fromCharCode(args[ai++] & 255);
        break;
      }
      case 115: {                                             // s
        numeric = false;
        const p: CPtr = args[ai++];
        let s = p === null ? "(null)" : strOf(p);
        if (prec >= 0 && s.length > prec) s = s.slice(0, prec);
        body = s;
        break;
      }
      case 102: case 70: case 101: case 69: case 103: case 71: {
        const v: number = args[ai++];
        if (prec < 0) prec = 6;
        if (signBit(v)) sign = "-"; else if (plus) sign = "+"; else if (space) sign = " ";
        if (v !== v || v === Infinity || v === -Infinity) {
          body = v !== v ? "nan" : "inf";
          if (conv === 70 || conv === 69 || conv === 71) body = body.toUpperCase();
          zero = false;
        } else body = formatFloat(Math.abs(v), conv, prec, alt);
        break;
      }
      default:
        throw new Error("printf %" + String.fromCharCode(conv));
    }
    const len = sign.length + body.length;
    if (len >= width) o += sign + body;
    else if (left) o += sign + body + " ".repeat(width - len);
    else if (zero && numeric) o += sign + "0".repeat(width - len) + body;
    else o += " ".repeat(width - len) + sign + body;
  }
  return o;
}
export function fprintf(f: CFile, fmt: CPtr, ...args: any[]): number {
  const s = format(fmt, args);
  if (f === null) return -1;
  f.writeStr(s);
  return s.length;
}
export function snprintf(b: CPtr, n: number, fmt: CPtr, ...args: any[]): number {
  const s = format(fmt, args);
  if (n > 0) {
    const m = Math.min(s.length, n - 1);
    for (let i = 0; i < m; i++) b.b[b.o + i] = s.charCodeAt(i);
    b.b[b.o + m] = 0;
  }
  return s.length;
}

// ------------------------------------------------------------------ files
const BUFSZ = 1 << 16;
export class CFile {
  fd: number;
  std: boolean;
  pos = 0;
  eof = false;
  wbuf: Uint8Array = null; wlen = 0; wpos = 0;              // pending writes, starting at file offset wpos
  rbuf: Uint8Array = null; rstart = 0; rlen = 0;            // cached bytes of the file at [rstart, rstart + rlen)
  constructor(fd: number, std: boolean) { this.fd = fd; this.std = std; }
  flush(): void {
    if (this.wlen > 0) {
      let off = 0;
      while (off < this.wlen) off += fs.writeSync(this.fd, this.wbuf, off, this.wlen - off, this.std ? null : this.wpos + off);
      this.wlen = 0;
    }
  }
  write(src: Uint8Array, off: number, n: number): void {
    this.rlen = 0;
    if (this.wbuf === null) this.wbuf = new Uint8Array(BUFSZ);
    if (this.wlen > 0 && this.pos !== this.wpos + this.wlen) this.flush();
    if (this.wlen === 0) this.wpos = this.pos;
    if (this.wlen + n > BUFSZ) {
      this.flush();
      if (n > BUFSZ) {
        let w = 0;
        while (w < n) w += fs.writeSync(this.fd, src, off + w, n - w, this.std ? null : this.pos + w);
        this.pos += n;
        return;
      }
      this.wpos = this.pos;
    }
    this.wbuf.set(src.subarray(off, off + n), this.wlen);
    this.wlen += n;
    this.pos += n;
  }
  writeStr(s: string): void {
    const n = s.length;
    const t = n <= tmpStr.length ? tmpStr : new Uint8Array(n);
    for (let i = 0; i < n; i++) t[i] = s.charCodeAt(i);
    this.write(t, 0, n);
  }
  // read up to n bytes at the current position; the count read
  read(dst: Uint8Array, off: number, n: number): number {
    this.flush();
    let got = 0;
    while (got < n) {
      if (this.rlen > 0 && this.pos >= this.rstart && this.pos < this.rstart + this.rlen) {
        const a = this.pos - this.rstart;
        const m = Math.min(n - got, this.rlen - a);
        dst.set(this.rbuf.subarray(a, a + m), off + got);
        got += m; this.pos += m;
        continue;
      }
      if (this.rbuf === null) this.rbuf = new Uint8Array(BUFSZ);
      this.rstart = this.pos;
      this.rlen = fs.readSync(this.fd, this.rbuf, 0, BUFSZ, this.pos);
      if (this.rlen === 0) break;
    }
    return got;
  }
  // next byte or -1 at end of file
  getc(): number {
    if (this.rlen > 0 && this.pos >= this.rstart && this.pos < this.rstart + this.rlen && this.wlen === 0) {
      return this.rbuf[this.pos++ - this.rstart];
    }
    const n = this.read(tmp1, 0, 1);
    return n === 1 ? tmp1[0] : -1;
  }
  size(): number { this.flush(); return fs.fstatSync(this.fd).size; }
}
const tmp1 = new Uint8Array(1);
const tmpStr = new Uint8Array(4096);
export const stdout = new CFile(1, true);

export function fopen(name: CPtr, mode: CPtr): CFile {
  const fn = strOf(name);
  const m = strOf(mode).replace(/[tb]/g, "");
  try {
    const fd = fs.openSync(fn, m);
    const f = new CFile(fd, false);
    if (m[0] === "a") f.pos = f.size();
    return f;
  } catch (e) {
    return null;
  }
}
export function fclose(f: CFile): number {
  if (f === null) return -1;
  f.flush();
  if (!f.std) fs.closeSync(f.fd);
  return 0;
}
export function fflush(f: CFile): number { if (f !== null) f.flush(); return 0; }
export function feof(f: CFile): number { return f.eof ? 1 : 0; }
export function rewind(f: CFile): void { f.flush(); f.pos = 0; f.eof = false; }
export function fseek(f: CFile, off: number, whence: number): number {
  f.flush();
  const p = whence === 0 ? off : whence === 1 ? f.pos + off : f.size() + off;
  if (p < 0) return -1;
  f.pos = p;
  f.eof = false;
  return 0;
}
export function ftell(f: CFile): number { return f.pos; }
export function fgets(b: CPtr, n: number, f: CFile): CPtr {
  let m = 0;
  while (m < n - 1) {
    const c = f.getc();
    if (c < 0) { f.eof = true; break; }
    b.b[b.o + m++] = c;
    if (c === 10) break;
  }
  if (m === 0) return null;
  b.b[b.o + m] = 0;
  return b;
}
export function remove(name: CPtr): number {
  try { fs.unlinkSync(strOf(name)); return 0; } catch (e) { return -1; }
}
export function mkstemp(template: CPtr): number {
  const n = strlen(template);
  const chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  for (let tries = 0; tries < 1000; tries++) {
    for (let i = n - 6; i < n; i++) template.s(i, chars.charCodeAt(Math.floor(Math.random() * chars.length)));
    try {
      const fd = fs.openSync(strOf(template), "wx", 0o600);
      fs.closeSync(fd);
      return 3;
    } catch (e) {
      continue;
    }
  }
  return -1;
}
export function realpathOf(p: string): string {
  try { return fs.realpathSync(p); } catch (e) { return path.resolve(p); }
}

// binary I/O of one value or an array; code: d double, f float, i int, I unsigned, q long, c char
const IO = new DataView(new ArrayBuffer(8));
const IOB = new Uint8Array(IO.buffer);
function packOne(code: string, v: number): number {
  switch (code) {
    case "d": IO.setFloat64(0, v, true); return 8;
    case "f": IO.setFloat32(0, v, true); return 4;
    case "i": IO.setInt32(0, v, true); return 4;
    case "I": IO.setUint32(0, v >>> 0, true); return 4;
    case "q": IO.setBigInt64(0, BigInt(v), true); return 8;
    case "c": IO.setUint8(0, v & 255); return 1;
  }
  throw new Error("fwrite code " + code);
}
function unpackOne(code: string): number {
  switch (code) {
    case "d": return IO.getFloat64(0, true);
    case "f": return IO.getFloat32(0, true);
    case "i": return IO.getInt32(0, true);
    case "I": return IO.getUint32(0, true);
    case "q": return Number(IO.getBigInt64(0, true));
    case "c": return (IO.getUint8(0) << 24) >> 24;
  }
  throw new Error("fread code " + code);
}
export function fwrite1(code: string, size: number, v: number, f: CFile): number {
  packOne(code, v);
  f.write(IOB, 0, size);                 // an 8-byte long written with sizeof(INT4): its low 4 bytes
  return 1;
}
export function fread1(code: string, size: number, r: Ref, f: CFile): number {
  packOne(code, Number.isFinite(r.v) ? r.v : 0);   // a short read keeps the upper bytes of the old value
  const got = f.read(IOB, 0, size);
  if (got < size) { f.eof = true; return 0; }
  r.v = unpackOne(code);
  return 1;
}
export function fwrite_arr(code: string, size: number, a: any, off: number, n: number, f: CFile): number {
  if (code === "c") { const p: CPtr = a; f.write(p.b, p.o + off, n); return n; }
  for (let i = 0; i < n; i++) { const m = packOne(code, a[off + i]); f.write(IOB, 0, m); }
  return n;
}
export function fread_arr(code: string, size: number, a: any, off: number, n: number, f: CFile): number {
  if (code === "c") {
    const p: CPtr = a;
    const got = f.read(p.b, p.o + off, n);
    if (got < n) f.eof = true;
    return got;
  }
  for (let i = 0; i < n; i++) {
    const m = packOne(code, 0);
    if (f.read(IOB, 0, m) < m) { f.eof = true; return i; }
    a[off + i] = unpackOne(code);
  }
  return n;
}

// ------------------------------------------------------------------ time
export function time(): number { return Math.floor(Date.now() / 1000); }
export function ctime(t: number): CPtr {
  const d = new Date(t * 1000);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const mons = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const p2 = (x: number) => (x < 10 ? "0" + x : String(x));
  const dd = d.getDate();
  return lit(days[d.getDay()] + " " + mons[d.getMonth()] + " " + (dd < 10 ? " " + dd : String(dd)) + " " +
    p2(d.getHours()) + ":" + p2(d.getMinutes()) + ":" + p2(d.getSeconds()) + " " + d.getFullYear() + "\n");
}
