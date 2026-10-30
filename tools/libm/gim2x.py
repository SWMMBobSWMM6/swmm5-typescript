#!/usr/bin/env python3
"""gim2x.py - translate GCC's optimized GIMPLE of glibc's math routines into TypeScript, Java or Go.

Why GIMPLE: on this machine glibc runs the FMA-compiled variants of exp, log, pow, sin, cos, tan, asin, acos
and atan (x86-64 ifuncs built with -mfma -mavx2), and SWMM's output depends on them bit for bit (43 of the
480 gate models change when glibc is made to use its generic variants). Which a*b+c GCC fused is decided by
the compiler, not the source, so the translation starts from the compiler's own final intermediate form
(-fdump-tree-optimized), where every fused operation is an explicit .FMA/.FNMA/.FMS/.FNMS. The sources are
compiled exactly as glibc's x86-64 multiarch Makefile compiles them; tools/libm/build_glibm.sh shows the
flags and checks the result against the system libm before translation.

  gim2x.py ts|java|go OUT DUMP... [--tables TABLES.json] [--name NAME=EXPORT ...] [--go-loop]

Written by Claude (Anthropic); see AI-USE.md.
"""
import json, os, re, sys, collections

# ----------------------------------------------------------------------------------------------- types
TYPES = {
    'double': 'f64', 'double_t': 'f64', 'const double': 'f64', 'float': 'f32',
    'int': 'i32', 'signed int': 'i32', 'int4': 'i32', 'int32_t': 'i32', 'const int': 'i32', 'short int': 'i32',
    'unsigned int': 'u32', 'uint32_t': 'u32', 'unsigned': 'u32',
    'long int': 'i64', 'signed long': 'i64', 'long': 'i64', 'int64_t': 'i64', 'long long int': 'i64',
    'long unsigned int': 'u64', 'unsigned long': 'u64', 'uint64_t': 'u64', 'sizetype': 'u64',
    'long long unsigned int': 'u64', '_Bool': 'bool',
}


def tkind(t):
    t = t.strip()
    if t in TYPES:
        return TYPES[t]
    raise ValueError('type ' + repr(t))


# ----------------------------------------------------------------------------------------------- parsing
FUNC_RE = re.compile(r'^(\w[\w ]*?)\s+(\w+)\s+\((.*)\)\s*$')
SSA_RE = re.compile(r'^([A-Za-z_][\w.]*?)_(\d+)(\(D\))?$')


class Func:
    def __init__(self, name, ret, params):
        self.name, self.ret, self.params = name, ret, params
        self.decls = {}
        self.blocks = collections.OrderedDict()


def parse(path):
    lines = open(path).read().split('\n')
    funcs = []
    i = 0
    while i < len(lines):
        m = FUNC_RE.match(lines[i])
        if m and i + 1 < len(lines) and lines[i + 1] == '{':
            ret, name, ps = m.group(1), m.group(2), m.group(3)
            params = []
            for p in [x.strip() for x in ps.split(',') if x.strip() and x.strip() != 'void']:
                pm = re.match(r'^(.*?)\s*(\w+)$', p)
                params.append((tkind(pm.group(1)), pm.group(2)))
            f = Func(name, tkind(ret) if ret.strip() != 'void' else None, params)
            i += 2
            cur = None
            while lines[i] != '}':
                s = lines[i].strip()
                i += 1
                if not s:
                    continue
                bm = re.match(r'^<bb (\d+)>', s)
                if bm:
                    cur = int(bm.group(1))
                    f.blocks[cur] = []
                    continue
                if cur is None:
                    f.__dict__.setdefault('decl_lines', []).append(s)
                    dm = re.match(r'^(.*?)\s+(\S+);$', s)
                    if dm and not s.startswith('static'):
                        try:
                            f.decls[dm.group(2)] = tkind(dm.group(1))
                        except ValueError:
                            f.decls[dm.group(2)] = 'other:' + dm.group(1)
                    continue
                s = re.sub(r';\s*\[tail call\]$', ';', s)
                s = re.sub(r'\s*\[(?:\d+\.\d+%|likely|unlikely)[^\]]*\]$', '', s) if s.startswith('goto') else s
                f.blocks[cur].append(s)
            funcs.append(f)
        i += 1
    return funcs


# ----------------------------------------------------------------------------------------------- emitter
FLOAT_RE = re.compile(r'^-?(\d+\.\d*(e[+-]?\d+)?|Inf|Nan)$')
INT_RE = re.compile(r'^-?\d+$')
TABLE_RE = re.compile(r'^([A-Za-z_]\w*)((?:\.\w+|\[[^\]]+\])+)$')


class Emitter:
    def __init__(self, lang, tables, rename):
        self.lang, self.tables, self.rename = lang, tables, rename
        self.used_tables = set()

    # --- names and kinds
    def vname(self, tok):
        m = SSA_RE.match(tok)
        if m:
            base = m.group(1).replace('.', '_')
            return 'p_' + base if m.group(3) else 'v%s_%s' % (base.lstrip('_') or 't', m.group(2))
        return 'l_' + tok.replace('.', '_')

    def kind(self, tok):
        if tok in getattr(self, 'ssa_kind', {}):
            return self.ssa_kind[tok]
        m = SSA_RE.match(tok)
        if m:
            if m.group(3):
                return self.params[m.group(1)]
            k = self.f.decls.get('_' + m.group(2)) if m.group(1) == '' else None
            if k is None:
                k = self.f.decls.get(tok) or self.f.decls.get(m.group(1)) or self.params.get(m.group(1))
            if k is None or k.startswith('other'):
                raise ValueError('kind of ' + tok + ' in ' + self.f.name)
            return k
        if tok in self.f.decls:
            return self.f.decls[tok]
        raise ValueError('kind of ' + tok)

    # --- literals
    def lit(self, tok, k):
        if k in ('f64', 'f32'):
            if tok in ('Inf', '-Inf'):
                return ('-' if tok[0] == '-' else '') + ('Infinity' if self.lang == 'ts' else 'Double.POSITIVE_INFINITY')
            if tok.endswith('Nan'):
                return 'NaN' if self.lang == 'ts' else 'Double.NaN'
            v = float(tok)
            if v == 0.0:
                return '-0.0' if tok.startswith('-') else '0.0'
            return repr(v)
        n = int(tok)
        if k in ('i64', 'u64'):
            n = n & 0xffffffffffffffff
            if k == 'i64' and n >= 1 << 63:
                n -= 1 << 64
            if k == 'u64' and n >= 1 << 63 and self.lang == 'java':
                n -= 1 << 64        # Java long holds the bit pattern
            return ('%dn' % n) if self.lang == 'ts' else ('%dL' % n)
        if k == 'bool':
            return 'true' if n else 'false'
        n = n & 0xffffffff
        if k == 'i32' or self.lang == 'java':
            if n >= 1 << 31:
                n -= 1 << 32
        return str(n)

    def opnd(self, tok, k=None):
        """code for an operand; k = expected kind (for literals)"""
        tok = tok.strip()
        if INT_RE.match(tok) or FLOAT_RE.match(tok) or tok in ('Inf', '-Inf'):
            return self.lit(tok, k or ('f64' if not INT_RE.match(tok) else 'i32')), k
        tm = TABLE_RE.match(tok)
        if tm and not SSA_RE.match(tok) and ('[' in tok or '.' in tok) and tm.group(1) not in self.f.decls:
            return self.table(tm.group(1), tm.group(2), k), k
        return self.vname(tok), self.kind(tok)

    def table(self, base, path, k):
        idx = re.findall(r'\[([^\]]+)\]', path)
        key = base + re.sub(r'\[[^\]]+\]', '[]', path)
        t = self.tables.get(key)
        if t is None:
            raise ValueError('table ' + key)
        self.used_tables.add(key)
        name = 'T_' + re.sub(r'\W+', '_', key).strip('_')
        if not idx:
            return name if t['kind'] in ('f64', 'i32', 'u32') or self.lang == 'java' else name
        ics = []
        for i in idx:
            c, ik = self.opnd(i, 'i32')
            if ik in ('i64', 'u64'):
                c = ('Number(%s)' % c) if self.lang == 'ts' else ('(int) (%s)' % c)
            ics.append(c)
        if len(ics) == 2:
            flat = '(%s) * %d + (%s)' % (ics[0], t['dims'][1], ics[1])
        else:
            flat = ics[0]
        return '%s[%s]' % (name, flat)

    # --- conversion between kinds
    def conv(self, code, src, dst):
        L = self.lang
        if src == dst:
            return code
        if L == 'ts':
            if dst == 'f64':
                if src in ('i64', 'u64'):
                    return 'Number(%s)' % code
                if src == 'bool':
                    return '(%s ? 1 : 0)' % code
                return code
            if dst == 'i32':
                if src in ('i64', 'u64'):
                    return 'Number(BigInt.asIntN(32, %s))' % code
                if src == 'f64':
                    return 'R.d2i(%s)' % code
                if src == 'bool':
                    return '(%s ? 1 : 0)' % code
                return '(%s | 0)' % code
            if dst == 'u32':
                if src in ('i64', 'u64'):
                    return 'Number(BigInt.asUintN(32, %s))' % code
                if src == 'f64':
                    return '(R.d2l(%s) >>> 0)' % code
                if src == 'bool':
                    return '(%s ? 1 : 0)' % code
                return '(%s >>> 0)' % code
            if dst == 'i64':
                if src == 'u64':
                    return 'BigInt.asIntN(64, %s)' % code
                if src == 'f64':
                    return 'R.d2ll(%s)' % code
                if src == 'bool':
                    return '(%s ? 1n : 0n)' % code
                return 'BigInt(%s)' % code
            if dst == 'u64':
                if src == 'i64':
                    return 'BigInt.asUintN(64, %s)' % code
                if src == 'i32':
                    return 'BigInt.asUintN(64, BigInt(%s))' % code
                if src == 'f64':
                    return 'BigInt.asUintN(64, R.d2ll(%s))' % code
                if src == 'bool':
                    return '(%s ? 1n : 0n)' % code
                return 'BigInt(%s)' % code
            if dst == 'bool':
                # GIMPLE converts an integer to _Bool (precision 1) by keeping the low bit: GCC folds the
                # source's one-bit test "iy & (1ULL << k)" into "(_Bool) (iy >> k)", so "!= 0" would be wrong
                if src in ('i64', 'u64'):
                    return '((%s & 1n) !== 0n)' % code
                if src == 'f64':
                    return '(%s !== 0)' % code
                return '((%s & 1) !== 0)' % code
        else:   # java: i32/u32 -> int, i64/u64 -> long (bit patterns)
            if dst == 'f64':
                if src == 'u32':
                    return '((double) Integer.toUnsignedLong(%s))' % code
                if src == 'u64':
                    return 'R.u64ToDouble(%s)' % code
                if src == 'bool':
                    return '(%s ? 1.0 : 0.0)' % code
                return '((double) %s)' % code
            if dst in ('i32', 'u32'):
                if src in ('i64', 'u64'):
                    return '((int) %s)' % code
                if src == 'f64':
                    return ('R.d2i(%s)' if dst == 'i32' else '((int) R.d2l(%s))') % code
                if src == 'bool':
                    return '(%s ? 1 : 0)' % code
                return code
            if dst in ('i64', 'u64'):
                if src == 'u32':
                    return 'Integer.toUnsignedLong(%s)' % code
                if src == 'i32':
                    return '((long) %s)' % code
                if src == 'f64':
                    return 'R.d2l(%s)' % code
                if src == 'bool':
                    return '(%s ? 1L : 0L)' % code
                return code
            if dst == 'bool':
                if src in ('i64', 'u64'):
                    return '((%s & 1L) != 0)' % code
                if src == 'f64':
                    return '(%s != 0)' % code
                return '((%s & 1) != 0)' % code
        raise ValueError('conv %s -> %s' % (src, dst))

    def wrap(self, code, k):
        """normalize an integer result to its kind"""
        if self.lang == 'ts':
            if k == 'i32':
                return '(%s | 0)' % code
            if k == 'u32':
                return '(%s >>> 0)' % code
            if k == 'i64':
                return 'BigInt.asIntN(64, %s)' % code
            if k == 'u64':
                return 'BigInt.asUintN(64, %s)' % code
        else:
            if k in ('i32', 'u32'):
                return '((int) (%s))' % code if 'long' in code else code
        return code

    # --- expressions
    def binop(self, a, op, b, k):
        ca, ka = self.opnd(a)
        cb, kb = self.opnd(b)
        if INT_RE.match(a.strip()) or FLOAT_RE.match(a.strip()):
            hint = k if (k and k != 'bool' and (op in ('<<', '>>') or not (INT_RE.match(b.strip()) or FLOAT_RE.match(b.strip())) and kb == k or op in ('<<', '>>'))) else kb
            ca, ka = self.opnd(a, hint)
        if INT_RE.match(b.strip()) or FLOAT_RE.match(b.strip()):
            cb, kb = self.opnd(b, ka if op not in ('<<', '>>') else ('i32' if ka in ('i32', 'u32') else ka))
        if op in ('<<', '>>') and self.lang == 'ts' and ka in ('i64', 'u64') and kb in ('i32', 'u32'):
            cb = 'BigInt(%s)' % cb
        if op in ('<<', '>>') and self.lang == 'ts' and ka in ('i32', 'u32') and kb in ('i64', 'u64'):
            cb = 'Number(%s)' % cb
        ok = ka
        L = self.lang
        if op in ('==', '!=', '<', '<=', '>', '>='):
            if L == 'ts':
                return '(%s %s %s)' % (ca, {'==': '===', '!=': '!=='}.get(op, op), cb)
            if ok in ('u32', 'u64') and op not in ('==', '!='):
                cmpf = 'Integer.compareUnsigned' if ok == 'u32' else 'Long.compareUnsigned'
                return '(%s(%s, %s) %s 0)' % (cmpf, ca, cb, op)
            return '(%s %s %s)' % (ca, op, cb)
        if ok in ('f64', 'f32'):
            return '(%s %s %s)' % (ca, op, cb)
        if ok == 'bool':
            return '(%s %s %s)' % (ca, {'&': '&&', '|': '||', '^': '!=='}[op] if L == 'ts' else {'&': '&', '|': '|', '^': '^'}[op], cb)
        if L == 'ts':
            if ok in ('i32', 'u32'):
                if op == '*':
                    return self.wrap('Math.imul(%s, %s)' % (ca, cb), k)
                if op == '/':
                    return ('((%s / %s) | 0)' if ok == 'i32' else '(Math.floor(%s / %s) >>> 0)') % (ca, cb)
                if op == '%':
                    return self.wrap('(%s %% %s)' % (ca, cb), k)
                if op == '>>':
                    return '(%s %s %s)' % (ca, '>>' if ok == 'i32' else '>>>', cb)
                return self.wrap('(%s %s %s)' % (ca, op, cb), k)
            # BigInt
            if op == '>>':
                return self.wrap('(%s >> %s)' % (ca, cb), k)    # BigInt values are normalized by kind
            return self.wrap('(%s %s %s)' % (ca, op, cb), k)
        # java
        if ok in ('u32', 'u64'):
            if op == '/':
                return '%s.divideUnsigned(%s, %s)' % ('Integer' if ok == 'u32' else 'Long', ca, cb)
            if op == '%':
                return '%s.remainderUnsigned(%s, %s)' % ('Integer' if ok == 'u32' else 'Long', ca, cb)
            if op == '>>':
                return '(%s >>> %s)' % (ca, cb)
        return '(%s %s %s)' % (ca, op, cb)

    def expr(self, rhs, k):
        """code for an rhs whose result has kind k"""
        L = self.lang
        rhs = rhs.strip()
        rhs = re.sub(r'\s*\[tail call\]$', '', rhs)
        m = re.match(r'^\.(FMA|FNMA|FMS|FNMS) \((.*), (.*), (.*)\)$', rhs)
        if m:
            a, b, c = (self.opnd(x, 'f64')[0] for x in m.groups()[1:])
            neg = lambda s: '(-%s)' % s
            op = m.group(1)
            if op in ('FNMA', 'FNMS'):
                a = neg(a)
            if op in ('FMS', 'FNMS'):
                c = neg(c)
            return ('R.fma(%s, %s, %s)' if L == 'ts' else 'Math.fma(%s, %s, %s)') % (a, b, c)
        m = re.match(r'^__builtin_fma \((.*), (.*), (.*)\)$', rhs)
        if m:
            a, b, c = (self.opnd(x, 'f64')[0] for x in m.groups())
            return ('R.fma(%s, %s, %s)' if L == 'ts' else 'Math.fma(%s, %s, %s)') % (a, b, c)
        m = re.match(r'^ABS_EXPR <(.*)>$', rhs)
        if m:
            c, ka = self.opnd(m.group(1), k)
            if ka in ('f64', 'f32'):
                return 'Math.abs(%s)' % c
            return self.wrap('Math.abs(%s)' % c, k) if L == 'ts' and ka == 'i32' else 'Math.abs(%s)' % c
        m = re.match(r'^(MIN|MAX)_EXPR <(.*), (.*)>$', rhs)
        if m:
            a, ka = self.opnd(m.group(2), k)
            b, kb = self.opnd(m.group(3), ka)
            op = '<' if m.group(1) == 'MIN' else '>'
            if ka in ('u32', 'u64') and L == 'java':
                cmpf = 'Integer.compareUnsigned' if ka == 'u32' else 'Long.compareUnsigned'
                return '(%s(%s, %s) %s 0 ? %s : %s)' % (cmpf, a, b, op, a, b)
            return '(%s %s %s ? %s : %s)' % (a, op, b, a, b)
        m = re.match(r'^VIEW_CONVERT_EXPR<(.*?)>\((.*)\)$', rhs)
        if m:
            dk = tkind(m.group(1))
            c, sk = self.opnd(m.group(2))
            if sk == 'f64' and dk in ('u64', 'i64'):
                return ('R.f2u(%s)' if dk == 'u64' else 'R.f2i(%s)') % c if L == 'ts' else 'Double.doubleToRawLongBits(%s)' % c
            if sk in ('u64', 'i64') and dk == 'f64':
                return ('R.u2f(%s)' if L == 'ts' else 'Double.longBitsToDouble(%s)') % c
            if sk == dk or {sk, dk} <= {'u64', 'i64'} or {sk, dk} <= {'u32', 'i32'}:
                return self.conv(c, sk, dk)
            raise ValueError('VIEW_CONVERT ' + rhs)
        m = re.match(r'^BIT_FIELD_REF <(.*), 32, (0|32)>$', rhs)
        if m:
            c, sk = self.opnd(m.group(1))
            assert sk == 'f64', rhs
            if L == 'ts':
                f = 'R.hi' if m.group(2) == '32' else 'R.lo'
                return ('(%s(%s) >>> 0)' if k == 'u32' else '%s(%s)') % (f, c)
            return '((int) (Double.doubleToRawLongBits(%s) %s))' % (c, '>>> 32' if m.group(2) == '32' else '')
        m = re.match(r'^\(([\w ]+)\) (\S+)$', rhs)
        if m and m.group(1).strip() in TYPES:
            dk = tkind(m.group(1))
            c, sk = self.opnd(m.group(2))
            return self.conv(c, sk, dk)
        m = re.match(r'^(\S+) (\+|-|\*|/|%|&|\||\^|<<|>>|==|!=|<=|>=|<|>|r<<|r>>) (\S+)$', rhs)
        if m:
            return self.binop(m.group(1), m.group(2), m.group(3), k)
        m = re.match(r'^(-|~|!)(\S+)$', rhs)
        if m and not (INT_RE.match(rhs) or FLOAT_RE.match(rhs)):
            c, ka = self.opnd(m.group(2), k)
            op = m.group(1)
            if ka in ('f64', 'f32'):
                return '(-%s)' % c
            if op == '!':
                return '(!%s)' % c
            if L == 'ts':
                if ka in ('i64', 'u64'):
                    return self.wrap('(%s%s)' % (op, c), k)
                return self.wrap('(%s%s)' % (op, c), k)
            return '(%s%s)' % (op, c)
        m = re.match(r'^(\S+) \? (\S+) : (\S+)$', rhs)
        if m:
            cc, _ = self.opnd(m.group(1))
            a, _ = self.opnd(m.group(2), k)
            b, _ = self.opnd(m.group(3), k)
            return '(%s ? %s : %s)' % (cc, a, b)
        m = re.match(r'^__branred \((\S+), &(\w+), &(\w+)\)$', rhs)
        if m:
            x, _ = self.opnd(m.group(1), 'f64')
            va, vb = 'l_' + m.group(2), 'l_' + m.group(3)
            if L == 'ts':
                return '(R.setToverp(T_toverp), (bri = R.branred(%s)), %s = R.brA, %s = R.brAA, bri)' % (x, va, vb)
            return 'R.branred(%s, br)' % x if False else '(bri = R.branred(%s, T_toverp)) + 0 * ((%s = R.brA) + (%s = R.brAA) > 0 ? 0 : 0)' % (x, va, vb)
        m = re.match(r'^(\w+) \((.*)\)$', rhs)
        if m:
            fn = m.group(1)
            args = [x for x in m.group(2).split(', ') if x] if m.group(2).strip() else []
            fname = self.rename.get(fn, fn)
            pk = self.sigs.get(fn)
            cargs = []
            for i, x in enumerate(args):
                ek = pk[i] if pk and i < len(pk) else None
                c, xk = self.opnd(x, ek)
                if ek and xk and xk != ek:
                    c = self.conv(c, xk, ek)
                cargs.append(c)
            return '%s(%s)' % (fname, ', '.join(cargs))
        c, ka = self.opnd(rhs, k)
        if ka and ka != k and k:
            c = self.conv(c, ka, k)
        return c

    # --- type inference for SSA names whose base variable name is declared with more than one type
    def infer(self, f, rets):
        self.ssa_kind = {}
        bases = collections.defaultdict(set)
        for line in self.decl_lines(f):
            dm = re.match(r'^(.*?)\s+(\S+);$', line)
            if dm:
                try:
                    bases[dm.group(2)].add(tkind(dm.group(1)))
                except ValueError:
                    pass
        ambiguous = {b for b, ks in bases.items() if len(ks) > 1}
        if not ambiguous:
            return
        defs = []
        for stmts in f.blocks.values():
            for st in stmts:
                m = re.match(r'^(?:# )?([\w.]+) = (.*);$', st) or re.match(r'^# ([\w.]+) = (PHI <.*>)$', st)
                if m:
                    sm = SSA_RE.match(m.group(1))
                    if sm and not sm.group(3) and sm.group(1) in ambiguous:
                        defs.append((m.group(1), m.group(2)))
        def known(tok):
            tok = tok.strip()
            if tok in self.ssa_kind:
                return self.ssa_kind[tok]
            if INT_RE.match(tok) or FLOAT_RE.match(tok):
                return None
            sm = SSA_RE.match(tok)
            if sm and sm.group(1) in ambiguous and not sm.group(3):
                return None
            try:
                return self.kind(tok)
            except (ValueError, KeyError):
                return None
        for _ in range(20):
            changed = False
            for lhs, rhs in defs:
                if lhs in self.ssa_kind:
                    continue
                k = None
                r = rhs.strip()
                mm = re.match(r'^\(([\w ]+)\) \S+$', r) or re.match(r'^VIEW_CONVERT_EXPR<(.*?)>', r)
                if mm and mm.group(1).strip() in TYPES:
                    k = tkind(mm.group(1))
                elif r.startswith('.F') or r.startswith('__builtin_fma'):
                    k = 'f64'
                elif r.startswith('PHI'):
                    for arm in re.findall(r'([^,<(]+(?:\(D\))?)\(\d+\)', r):
                        k = known(arm)
                        if k:
                            break
                elif re.match(r'^(\w+) \(', r):
                    k = rets.get(re.match(r'^(\w+)', r).group(1))
                else:
                    bm = re.match(r'^(\S+) (==|!=|<=|>=|<|>) (\S+)$', r)
                    if bm:
                        k = 'bool'
                    else:
                        toks = re.findall(r'[A-Za-z_][\w.]*(?:\(D\))?', re.sub(r'^(ABS_EXPR|MIN_EXPR|MAX_EXPR|BIT_FIELD_REF)', '', r))
                        for t in toks:
                            k = known(t)
                            if k:
                                break
                        if r.startswith('BIT_FIELD_REF'):
                            k = 'i32'
                if k:
                    self.ssa_kind[lhs] = k
                    changed = True
            if not changed:
                break
        missing = [l for l, _ in defs if l not in self.ssa_kind]
        if missing:
            raise ValueError('cannot type %s in %s' % (missing[:5], f.name))

    def decl_lines(self, f):
        return getattr(f, 'decl_lines', [])

    # --- function body
    def function(self, f, sigs):
        self.f = f
        self.sigs = sigs
        self.params = {n: k for k, n in f.params}
        self.ssa_kind = {}
        self.infer(f, self.rets)
        L = self.lang
        out = []
        fname = self.rename.get(f.name, f.name)
        tsig = {'f64': 'number', 'f32': 'number', 'i32': 'number', 'u32': 'number', 'i64': 'bigint', 'u64': 'bigint', 'bool': 'boolean'}
        jsig = {'f64': 'double', 'f32': 'float', 'i32': 'int', 'u32': 'int', 'i64': 'long', 'u64': 'long', 'bool': 'boolean'}
        if L == 'ts':
            ps = ', '.join('p_%s: %s' % (n, tsig[k]) for k, n in f.params)
            out.append('export function %s(%s): %s {' % (fname, ps, tsig[f.ret] if f.ret else 'void'))
        else:
            ps = ', '.join('%s p_%s' % (jsig[k], n) for k, n in f.params)
            out.append('  static %s %s(%s) {' % (jsig[f.ret] if f.ret else 'void', fname, ps))
        # collect assigned names and their kinds
        assigned = collections.OrderedDict()
        for b, stmts in f.blocks.items():
            for s in stmts:
                m = re.match(r'^(?:# )?([\w.]+(?:\(D\))?) = ', s)
                if m and '{CLOBBER' not in s and not s.startswith('if ('):
                    nm = m.group(1)
                    if TABLE_RE.match(nm) and ('.' in nm and not SSA_RE.match(nm)):
                        continue
                    assigned[self.vname(nm)] = self.kind(nm)
        for nm, k in f.decls.items():
            if not SSA_RE.match(nm) and not nm.startswith('_') or nm.startswith('__'):
                if k in tsig and self.vname(nm) not in assigned and not SSA_RE.match(nm):
                    assigned[self.vname(nm)] = k
        if any('__branred' in s for st in f.blocks.values() for s in st):
            assigned['bri'] = 'i32'
        ind = '  ' if L == 'ts' else '    '
        zero = {'f64': '0.0', 'f32': '0.0', 'i32': '0', 'u32': '0', 'i64': '0n' if L == 'ts' else '0L',
                'u64': '0n' if L == 'ts' else '0L', 'bool': 'false'}
        for v, k in assigned.items():
            if L == 'ts':
                out.append('%slet %s: %s = %s;' % (ind, v, tsig[k], zero[k]))
            else:
                out.append('%s%s %s = %s;' % (ind, jsig[k], v, zero[k]))
        order = list(f.blocks)
        out.append('%slet bb = %d, prev = 0;' % (ind, order[0]) if L == 'ts' else '%sint bb = %d, prev = 0;' % (ind, order[0]))
        out.append('%sfor (;;) {' % ind)
        out.append('%s  switch (bb) {' % ind)
        for bi, b in enumerate(order):
            stmts = f.blocks[b]
            out.append('%s    case %d: {' % (ind, b))
            body = ind + '      '
            phis = [s for s in stmts if s.startswith('# ')]
            if phis:
                # parallel copy: evaluate all incoming values first
                tmps = []
                for j, s in enumerate(phis):
                    m = re.match(r'^# (\S+) = PHI <(.*)>$', s)
                    lhs = m.group(1)
                    lk = self.kind(lhs)
                    arms = []
                    for arm in re.findall(r'([^,(]+(?:\(D\))?)\((\d+)\)', m.group(2)):
                        val, frm = arm[0].strip(), int(arm[1])
                        c, vk = self.opnd(val, lk)
                        if vk and vk != lk:
                            c = self.conv(c, vk, lk)
                        arms.append((frm, c))
                    e = arms[-1][1]
                    for frm, c in reversed(arms[:-1]):
                        e = '(prev === %d ? %s : %s)' % (frm, c, e) if L == 'ts' else '(prev == %d ? %s : %s)' % (frm, c, e)
                    t = 'phi%d' % j
                    out.append('%s%s %s = %s;' % (body, 'const' if L == 'ts' else jsig[lk], t, e))
                    tmps.append((self.vname(lhs), t))
                for v, t in tmps:
                    out.append('%s%s = %s;' % (body, v, t))
            si = [s for s in stmts if not s.startswith('# ')]
            ended = False
            i = 0
            while i < len(si):
                s = si[i]
                i += 1
                if '{CLOBBER' in s or s.startswith('__asm__') or re.match(r'^(<L\d+>|\w+):$', s) or s.startswith('//'):
                    continue
                m = re.match(r'^if \((.*)\)$', s)
                if m:
                    cond = m.group(1)
                    cm = re.match(r'^(\S+) (==|!=|<=|>=|<|>) (\S+)$', cond)
                    if cm:
                        cc = self.binop(cm.group(1), cm.group(2), cm.group(3), 'bool')
                    else:
                        c, ck = self.opnd(cond)
                        cc = self.conv(c, ck, 'bool')
                    t1 = int(re.match(r'^goto <bb (\d+)>', si[i]).group(1))
                    assert si[i + 1] == 'else', si[i + 1]
                    t2 = int(re.match(r'^goto <bb (\d+)>', si[i + 2]).group(1))
                    i += 3
                    out.append('%sprev = %d; bb = %s ? %d : %d; continue;' % (body, b, cc, t1, t2))
                    ended = True
                    break
                m = re.match(r'^goto <bb (\d+)>;', s)
                if m:
                    out.append('%sprev = %d; bb = %s; continue;' % (body, b, m.group(1)))
                    ended = True
                    break
                m = re.match(r'^return(?: (.*))?;$', s)
                if m:
                    if m.group(1):
                        c, rk = self.opnd(m.group(1), f.ret)
                        if rk and rk != f.ret:
                            c = self.conv(c, rk, f.ret)
                        out.append('%sreturn %s;' % (body, c))
                    else:
                        out.append('%sreturn;' % body)
                    ended = True
                    break
                m = re.match(r'^([\w.]+(?:\(D\))?) = (.*);$', s)
                if m:
                    lhs, rhs = m.group(1), m.group(2)
                    lk = self.kind(lhs)
                    out.append('%s%s = %s;' % (body, self.vname(lhs), self.expr(rhs, lk)))
                    continue
                m = re.match(r'^(\w+) \((.*)\);$', s)
                if m:
                    out.append('%s%s;' % (body, self.expr(s[:-1], None)))
                    continue
                raise ValueError('statement %r in %s' % (s, f.name))
            if not ended:
                nxt = order[bi + 1]
                out.append('%sprev = %d; bb = %d; continue;' % (body, b, nxt))
            out.append('%s    }' % ind)
        out.append('%s    default: throw new %s("bad block " + bb);' % (ind, 'Error' if L == 'ts' else 'IllegalStateException'))
        out.append('%s  }' % ind)
        out.append('%s}' % ind)
        out.append('}' if L == 'ts' else '  }')
        return '\n'.join(out)



# ----------------------------------------------------------------------------------------------- Go
GT = {'f64': 'float64', 'f32': 'float32', 'i32': 'int32', 'u32': 'uint32', 'i64': 'int64', 'u64': 'uint64', 'bool': 'bool'}
GO_LIT_RE = re.compile(r'^float64\((-?[0-9.]+(?:e[+-]?\d+)?)\)$')


class GoEmitter(Emitter):
    """Go has native 32/64-bit signed and unsigned integers with C's wrapping, an exactly rounded
    math.FMA and bit casts, so the translation is direct. Three things need care: every floating-point
    operation is wrapped in float64(...), which the Go spec says forbids fusing it into a multiply-add
    and also forces rounding where Go would otherwise evaluate a constant expression exactly; Go has no
    negative-zero constant, so -0.0 is a variable; and math.NaN() is not the pattern glibc uses.
    Unlike TypeScript and Java, Go has goto, so the blocks are labels rather than cases of a dispatch loop."""

    def lit(self, tok, k):
        if k in ('f64', 'f32'):
            if tok in ('Inf', '-Inf'):
                return 'math.Inf(%d)' % (-1 if tok[0] == '-' else 1)
            if tok.endswith('Nan'):
                return 'qNaN'
            v = float(tok)
            if v == 0.0:
                return 'negZero' if tok.startswith('-') else 'float64(0)'
            return 'float64(%s)' % repr(v)
        n = int(tok)
        if k == 'bool':
            return 'true' if n else 'false'
        if k in ('i64', 'u64'):
            n &= 0xffffffffffffffff
            if k == 'i64' and n >= 1 << 63:
                n -= 1 << 64
            return '%s(%d)' % (GT[k], n)
        n &= 0xffffffff
        if k != 'u32' and n >= 1 << 31:
            n -= 1 << 32
        return '%s(%d)' % (GT.get(k, 'int32'), n)

    def table(self, base, path, k):
        idx = re.findall(r'\[([^\]]+)\]', path)
        key = base + re.sub(r'\[[^\]]+\]', '[]', path)
        t = self.tables.get(key)
        if t is None:
            raise ValueError('table ' + key)
        self.used_tables.add(key)
        name = 'T_' + re.sub(r'\W+', '_', key).strip('_')
        if not idx:
            return name
        ics = ['int(%s)' % self.opnd(i, 'i32')[0] for i in idx]
        flat = '%s*%d + %s' % (ics[0], t['dims'][1], ics[1]) if len(ics) == 2 else ics[0]
        return '%s[%s]' % (name, flat)

    def conv(self, code, src, dst):
        if src == dst:
            return code
        if dst == 'bool':
            if src == 'f64':
                return '(%s != 0)' % code
            # GIMPLE's (_Bool) keeps the low bit (see the TypeScript branch above)
            return '(%s&1 != 0)' % code
        if src == 'bool':
            return 'btoi[%s](%s)' % (GT[dst], code)
        if src == 'f64' and dst in ('i32', 'u32', 'i64', 'u64'):
            return {'i32': 'd2i(%s)', 'u32': 'uint32(d2l(%s))', 'i64': 'd2l(%s)', 'u64': 'uint64(d2l(%s))'}[dst] % code
        return '%s(%s)' % (GT[dst], code)

    def wrap(self, code, k):
        return code

    def neg(self, code):
        m = GO_LIT_RE.match(code)
        if m:
            v = -float(m.group(1))
            return 'negZero' if v == 0.0 and str(v).startswith('-') else 'float64(%s)' % repr(v)
        if code == 'float64(0)':
            return 'negZero'
        if code == 'negZero':
            return 'float64(0)'
        return '(-%s)' % code

    def binop(self, a, op, b, k):
        ca, ka = self.opnd(a)
        cb, kb = self.opnd(b)
        if INT_RE.match(a.strip()) or FLOAT_RE.match(a.strip()):
            hint = k if (k and k != 'bool' and (op in ('<<', '>>') or not (INT_RE.match(b.strip()) or FLOAT_RE.match(b.strip())) and kb == k or op in ('<<', '>>'))) else kb
            ca, ka = self.opnd(a, hint)
        if INT_RE.match(b.strip()) or FLOAT_RE.match(b.strip()):
            cb, kb = self.opnd(b, ka if op not in ('<<', '>>') else ('i32' if ka in ('i32', 'u32') else ka))
        if op in ('==', '!=', '<', '<=', '>', '>='):
            return '(%s %s %s)' % (ca, op, cb)
        if ka == 'bool':
            return '(%s %s %s)' % (ca, {'&': '&&', '|': '||', '^': '!='}[op], cb)
        if ka in ('f64', 'f32'):
            return '%s(%s %s %s)' % (GT[ka], ca, op, cb)
        return '(%s %s %s)' % (ca, op, cb)

    def expr(self, rhs, k):
        rhs = re.sub(r'\s*\[tail call\]$', '', rhs.strip())
        m = re.match(r'^\.(FMA|FNMA|FMS|FNMS) \((.*), (.*), (.*)\)$', rhs)
        if m:
            a, b, c = (self.opnd(x, 'f64')[0] for x in m.groups()[1:])
            op = m.group(1)
            if op in ('FNMA', 'FNMS'):
                a = self.neg(a)
            if op in ('FMS', 'FNMS'):
                c = self.neg(c)
            return 'math.FMA(%s, %s, %s)' % (a, b, c)
        m = re.match(r'^__builtin_fma \((.*), (.*), (.*)\)$', rhs)
        if m:
            return 'math.FMA(%s, %s, %s)' % tuple(self.opnd(x, 'f64')[0] for x in m.groups())
        m = re.match(r'^ABS_EXPR <(.*)>$', rhs)
        if m:
            c, ka = self.opnd(m.group(1), k)
            return ('math.Abs(%s)' if ka in ('f64', 'f32') else 'iabs(%s)') % c
        m = re.match(r'^(MIN|MAX)_EXPR <(.*), (.*)>$', rhs)
        if m:
            a, ka = self.opnd(m.group(2), k)
            b, kb = self.opnd(m.group(3), ka)
            return '%s(%s, %s)' % ('mn' if m.group(1) == 'MIN' else 'mx', a, b)
        m = re.match(r'^VIEW_CONVERT_EXPR<(.*?)>\((.*)\)$', rhs)
        if m:
            dk = tkind(m.group(1))
            c, sk = self.opnd(m.group(2))
            if sk == 'f64' and dk in ('u64', 'i64'):
                return ('math.Float64bits(%s)' if dk == 'u64' else 'int64(math.Float64bits(%s))') % c
            if sk in ('u64', 'i64') and dk == 'f64':
                return ('math.Float64frombits(%s)' if sk == 'u64' else 'math.Float64frombits(uint64(%s))') % c
            if sk == dk or {sk, dk} <= {'u64', 'i64'} or {sk, dk} <= {'u32', 'i32'}:
                return self.conv(c, sk, dk)
            raise ValueError('VIEW_CONVERT ' + rhs)
        m = re.match(r'^BIT_FIELD_REF <(.*), 32, (0|32)>$', rhs)
        if m:
            c, sk = self.opnd(m.group(1))
            assert sk == 'f64', rhs
            t = 'uint32' if k == 'u32' else 'int32'
            if m.group(2) == '32':
                return '%s(math.Float64bits(%s) >> 32)' % (t, c)
            return '%s(uint32(math.Float64bits(%s)))' % (t, c)
        m = re.match(r'^\(([\w ]+)\) (\S+)$', rhs)
        if m and m.group(1).strip() in TYPES:
            c, sk = self.opnd(m.group(2))
            return self.conv(c, sk, tkind(m.group(1)))
        m = re.match(r'^(\S+) (\+|-|\*|/|%|&|\||\^|<<|>>|==|!=|<=|>=|<|>) (\S+)$', rhs)
        if m:
            return self.binop(m.group(1), m.group(2), m.group(3), k)
        m = re.match(r'^(-|~|!)(\S+)$', rhs)
        if m and not (INT_RE.match(rhs) or FLOAT_RE.match(rhs)):
            c, ka = self.opnd(m.group(2), k)
            op = m.group(1)
            if ka in ('f64', 'f32'):
                return self.neg(c)
            return '(%s%s)' % ({'~': '^'}.get(op, op), c)
        m = re.match(r'^(\S+) \? (\S+) : (\S+)$', rhs)
        if m:
            cc, ck = self.opnd(m.group(1))
            a, _ = self.opnd(m.group(2), k)
            b, _ = self.opnd(m.group(3), k)
            return 'ifv(%s, %s, %s)' % (self.conv(cc, ck, 'bool') if ck != 'bool' else cc, a, b)
        m = re.match(r'^__branred \((\S+), &(\w+), &(\w+)\)$', rhs)
        if m:
            x, _ = self.opnd(m.group(1), 'f64')
            return 'branred(%s, T_toverp, &l_%s, &l_%s)' % (x, m.group(2), m.group(3))
        m = re.match(r'^(\w+) \((.*)\)$', rhs)
        if m:
            fn = m.group(1)
            args = [x for x in m.group(2).split(', ') if x] if m.group(2).strip() else []
            pk = self.sigs.get(fn)
            cargs = []
            for i, x in enumerate(args):
                ek = pk[i] if pk and i < len(pk) else None
                c, xk = self.opnd(x, ek)
                if ek and xk and xk != ek:
                    c = self.conv(c, xk, ek)
                cargs.append(c)
            return '%s(%s)' % (self.rename.get(fn, fn), ', '.join(cargs))
        c, ka = self.opnd(rhs, k)
        if ka and k and ka != k:
            c = self.conv(c, ka, k)
        return c

    def function(self, f, sigs):
        self.f = f
        self.sigs = sigs
        self.params = {n: k for k, n in f.params}
        self.ssa_kind = {}
        self.infer(f, self.rets)
        fname = self.rename.get(f.name, f.name)
        out = ['func %s(%s)%s {' % (fname, ', '.join('p_%s %s' % (n, GT[k]) for k, n in f.params),
                                    (' ' + GT[f.ret]) if f.ret else '')]
        assigned = collections.OrderedDict()
        for b, stmts in f.blocks.items():
            for s in stmts:
                m = re.match(r'^(?:# )?([\w.]+(?:\(D\))?) = ', s)
                if m and '{CLOBBER' not in s and not s.startswith('if ('):
                    nm = m.group(1)
                    if TABLE_RE.match(nm) and ('.' in nm and not SSA_RE.match(nm)):
                        continue
                    assigned[self.vname(nm)] = self.kind(nm)
        for nm, k in f.decls.items():
            if not SSA_RE.match(nm) and not nm.startswith('_') or nm.startswith('__'):
                if k in GT and self.vname(nm) not in assigned and not SSA_RE.match(nm):
                    assigned[self.vname(nm)] = k
        params = {'p_' + n for _, n in f.params}
        assigned = collections.OrderedDict((v, k) for v, k in assigned.items() if v not in params)
        for v, k in assigned.items():
            out.append('\tvar %s %s' % (v, GT[k]))
        names = list(assigned)
        for i in range(0, len(names), 8):
            chunk = names[i:i + 8]
            out.append('\t%s = %s' % (', '.join('_' for _ in chunk), ', '.join(chunk)))
        if getattr(self, 'loop', False):
            return self.loop_body(f, out)
        # Go has goto: every basic block is a label, every edge a jump, and each PHI is a parallel
        # assignment on the edge that feeds it, so the compiler sees the ordinary control-flow graph
        order = list(f.blocks)
        phi_in = {}            # block -> [(variable, kind, {pred: code})]
        for b in order:
            lst = []
            for s in f.blocks[b]:
                if not s.startswith('# '):
                    continue
                m = re.match(r'^# (\S+) = PHI <(.*)>$', s)
                lhs = m.group(1)
                lk = self.kind(lhs)
                arms = {}
                for arm in re.findall(r'([^,(]+(?:\(D\))?)\((\d+)\)', m.group(2)):
                    val, frm = arm[0].strip(), int(arm[1])
                    c, vk = self.opnd(val, lk)
                    if vk and vk != lk:
                        c = self.conv(c, vk, lk)
                    arms[frm] = c
                lst.append((self.vname(lhs), lk, arms))
            phi_in[b] = lst
        targets = set()

        def jump(frm, to, ind):
            """the copies for edge frm -> to, then the jump"""
            targets.add(to)
            lines = []
            ph = phi_in.get(to) or []
            if ph:
                missing = [v for v, _, arms in ph if frm not in arms]
                if missing:
                    raise ValueError('edge %d -> %d has no PHI argument for %s in %s' % (frm, to, missing, f.name))
                lines.append('%s%s = %s' % (ind, ', '.join(v for v, _, _ in ph), ', '.join(arms[frm] for _, _, arms in ph)))
            lines.append('%sgoto bb%d' % (ind, to))
            return lines

        blocks = []
        for bi, b in enumerate(order):
            stmts = f.blocks[b]
            body = '\t'
            lines = []
            si = [s for s in stmts if not s.startswith('# ')]
            ended = False
            i = 0
            while i < len(si):
                s = si[i]
                i += 1
                if '{CLOBBER' in s or s.startswith('__asm__') or re.match(r'^(<L\d+>|\w+):$', s) or s.startswith('//'):
                    continue
                m = re.match(r'^if \((.*)\)$', s)
                if m:
                    cond = m.group(1)
                    cm = re.match(r'^(\S+) (==|!=|<=|>=|<|>) (\S+)$', cond)
                    if cm:
                        cc = self.binop(cm.group(1), cm.group(2), cm.group(3), 'bool')
                    else:
                        c, ck = self.opnd(cond)
                        cc = self.conv(c, ck, 'bool')
                    t1 = int(re.match(r'^goto <bb (\d+)>', si[i]).group(1))
                    assert si[i + 1] == 'else', si[i + 1]
                    t2 = int(re.match(r'^goto <bb (\d+)>', si[i + 2]).group(1))
                    i += 3
                    lines.append('%sif %s {' % (body, cc))
                    lines += jump(b, t1, body + '\t')
                    lines.append('%s}' % body)
                    lines += jump(b, t2, body)
                    ended = True
                    break
                m = re.match(r'^goto <bb (\d+)>;', s)
                if m:
                    lines += jump(b, int(m.group(1)), body)
                    ended = True
                    break
                m = re.match(r'^return(?: (.*))?;$', s)
                if m:
                    if m.group(1):
                        c, rk = self.opnd(m.group(1), f.ret)
                        if rk and rk != f.ret:
                            c = self.conv(c, rk, f.ret)
                        lines.append('%sreturn %s' % (body, c))
                    else:
                        lines.append('%sreturn' % body)
                    ended = True
                    break
                m = re.match(r'^([\w.]+(?:\(D\))?) = (.*);$', s)
                if m:
                    lhs, rhs = m.group(1), m.group(2)
                    lines.append('%s%s = %s' % (body, self.vname(lhs), self.expr(rhs, self.kind(lhs))))
                    continue
                m = re.match(r'^(\w+) \((.*)\);$', s)
                if m:
                    lines.append('%s%s' % (body, self.expr(s[:-1], None)))
                    continue
                raise ValueError('statement %r in %s' % (s, f.name))
            if not ended:
                lines += jump(b, order[bi + 1], body)
            blocks.append((b, lines))
        if phi_in.get(order[0]):
            raise ValueError('PHI in the entry block of %s' % f.name)
        for b, lines in blocks:
            if b in targets:
                out.append('bb%d:' % b)
            out += lines
        if not out[-1].strip().startswith(('goto ', 'return')):
            out.append('\tpanic("unreachable")')
        out.append('}')
        return '\n'.join(out)

    def loop_body(self, f, out):
        """--go-loop: the TypeScript/Java shape (a loop dispatching on the block number, PHIs chosen by the
        previous block), kept only to measure what the goto form saves (gate/go_powtime.sh)"""
        order = list(f.blocks)
        out.append('\tbb, prev := int32(%d), int32(0)' % order[0])
        out.append('\t_ = prev')
        out.append('\tfor {')
        out.append('\t\tswitch bb {')
        for bi, b in enumerate(order):
            stmts = f.blocks[b]
            out.append('\t\tcase %d:' % b)
            body = '\t\t\t'
            phis = [s for s in stmts if s.startswith('# ')]
            tmps = []
            for j, s in enumerate(phis):
                m = re.match(r'^# (\S+) = PHI <(.*)>$', s)
                lhs = m.group(1)
                lk = self.kind(lhs)
                arms = []
                for arm in re.findall(r'([^,(]+(?:\(D\))?)\((\d+)\)', m.group(2)):
                    val, frm = arm[0].strip(), int(arm[1])
                    c, vk = self.opnd(val, lk)
                    if vk and vk != lk:
                        c = self.conv(c, vk, lk)
                    arms.append((frm, c))
                t = 'phi%d' % j
                out.append('%svar %s %s' % (body, t, GT[lk]))
                if len(arms) == 1:
                    out.append('%s%s = %s' % (body, t, arms[0][1]))
                else:
                    line = body
                    for ai, (frm, c) in enumerate(arms[:-1]):
                        line += ('if' if ai == 0 else ' else if') + ' prev == %d {\n%s\t%s = %s\n%s}' % (frm, body, t, c, body)
                    line += ' else {\n%s\t%s = %s\n%s}' % (body, t, arms[-1][1], body)
                    out.append(line)
                tmps.append((self.vname(lhs), t))
            for v, t in tmps:
                out.append('%s%s = %s' % (body, v, t))
            si = [s for s in stmts if not s.startswith('# ')]
            ended = False
            i = 0
            while i < len(si):
                s = si[i]
                i += 1
                if '{CLOBBER' in s or s.startswith('__asm__') or re.match(r'^(<L\d+>|\w+):$', s) or s.startswith('//'):
                    continue
                m = re.match(r'^if \((.*)\)$', s)
                if m:
                    cond = m.group(1)
                    cm = re.match(r'^(\S+) (==|!=|<=|>=|<|>) (\S+)$', cond)
                    if cm:
                        cc = self.binop(cm.group(1), cm.group(2), cm.group(3), 'bool')
                    else:
                        c, ck = self.opnd(cond)
                        cc = self.conv(c, ck, 'bool')
                    t1 = int(re.match(r'^goto <bb (\d+)>', si[i]).group(1))
                    assert si[i + 1] == 'else', si[i + 1]
                    t2 = int(re.match(r'^goto <bb (\d+)>', si[i + 2]).group(1))
                    i += 3
                    out.append('%sprev = %d' % (body, b))
                    out.append('%sif %s {\n%s\tbb = %d\n%s} else {\n%s\tbb = %d\n%s}' % (body, cc, body, t1, body, body, t2, body))
                    out.append('%scontinue' % body)
                    ended = True
                    break
                m = re.match(r'^goto <bb (\d+)>;', s)
                if m:
                    out.append('%sprev, bb = %d, %s' % (body, b, m.group(1)))
                    out.append('%scontinue' % body)
                    ended = True
                    break
                m = re.match(r'^return(?: (.*))?;$', s)
                if m:
                    if m.group(1):
                        c, rk = self.opnd(m.group(1), f.ret)
                        if rk and rk != f.ret:
                            c = self.conv(c, rk, f.ret)
                        out.append('%sreturn %s' % (body, c))
                    else:
                        out.append('%sreturn' % body)
                    ended = True
                    break
                m = re.match(r'^([\w.]+(?:\(D\))?) = (.*);$', s)
                if m:
                    lhs, rhs = m.group(1), m.group(2)
                    out.append('%s%s = %s' % (body, self.vname(lhs), self.expr(rhs, self.kind(lhs))))
                    continue
                m = re.match(r'^(\w+) \((.*)\);$', s)
                if m:
                    out.append('%s%s' % (body, self.expr(s[:-1], None)))
                    continue
                raise ValueError('statement %r in %s' % (s, f.name))
            if not ended:
                out.append('%sprev, bb = %d, %d' % (body, b, order[bi + 1]))
                out.append('%scontinue' % body)
        out.append('\t\tdefault:')
        out.append('\t\t\tpanic("bad block")')
        out.append('\t\t}')
        out.append('\t}')
        out.append('}')
        return '\n'.join(out)


# glibc 2.39's x86-64 log10, asin and acos symbols are still the SVID compatibility wrappers
# (math/w_log10_compat.c, w_asin_compat.c, w_acos_compat.c). On a domain error they return
# __kernel_standard's NAN, whose sign bit is clear, not the IEEE routine's (x-x)/(x-x), whose sign bit is
# set on x86 (printf shows "nan" for one and "-nan" for the other). Hand ports, appended to the output.
WRAPPERS_TS = [
    ('pow_exact', '''// Fast path of pow for the common case (x positive and normal, y neither tiny nor huge, result not near
// overflow or underflow). It computes exactly what pow_exact computes on that path (GCC's GIMPLE for glibc's
// pow, blocks 46 -> 54 -> 64, and 46 -> 47 -> 48 for |y log x| < 2^-54), with two changes of method that
// do not change a bit: the 64-bit integer steps use 32-bit halves instead of BigInt, and four of the
// fused multiply-adds are computed by cheaper exact means -- fma(z, invc, -1) is exactly representable
// (glibc's own comment on log_inline), kd * ln2hi is an exact product (ln2hi has 42 significant bits and
// |kd| < 2^10), and fma(a, b, -RN(a*b)) is the exact rounding error of a product (Dekker). Every other case
// goes to pow_exact. Hand-written by Claude (Anthropic); checked bit for bit against the system libm and
// against pow_exact (tools/libm/test).
const PF = new Float64Array(1), PW = new Int32Array(PF.buffer);
const ETd = new Float64Array(T_exp_data_tab.buffer), ETw = new Int32Array(T_exp_data_tab.buffer);
const SPLIT = 134217729;
function prodErr(a: number, b: number, p: number): number {   // a*b - p exactly, for p = RN(a*b)
  let t = SPLIT * a; const ah = t - (t - a), al = a - ah;
  t = SPLIT * b; const bh = t - (t - b), bl = b - bh;
  return ((ah * bh - p) + ah * bl + al * bh) + al * bl;
}
export function pow(x: number, y: number): number {
  PF[0] = x; const xhi = PW[1], xlo = PW[0];
  PF[0] = y; const yhi = PW[1];
  const topx = xhi >>> 20, topy = (yhi >>> 20) & 0x7ff;
  if (((topx - 1) >>> 0) > 2045 || ((topy - 958) >>> 0) > 127) return pow_exact(x, y);
  // log_inline(ix): tmp = ix - 0x3fe6955500000000 (its low word is zero, so no borrow)
  const tmphi = (xhi - 0x3fe69555) | 0;
  const i = (tmphi >>> 13) & 127;
  const kd = tmphi >> 20;
  PW[1] = (xhi - (tmphi & 0xfff00000)) | 0; PW[0] = xlo;
  const z = PF[0];
  const invc = T_pow_log_data_tab_invc[i], logc = T_pow_log_data_tab_logc[i], logctail = T_pow_log_data_tab_logctail[i];
  const zc = z * invc;
  const r = (zc - 1.0) + prodErr(z, invc, zc);              // fma(z, invc, -1), exactly representable
  const t1 = kd * T_pow_log_data_ln2hi + logc;               // fma(kd, ln2hi, logc): the product is exact
  const t2 = r + t1;
  const lo1 = R.fma(kd, T_pow_log_data_ln2lo, logctail);
  const lo2 = r + (t1 - t2);
  const P = T_pow_log_data_poly;
  const ar = r * P[0], ar2 = r * ar, ar3 = r * ar2;
  const hi = t2 + ar2;
  const lo3 = prodErr(r, ar, ar2);                           // fma(r, ar, -ar2)
  const lo4 = ar2 + (t2 - hi);
  const p12 = R.fma(r, P[2], P[1]), p34 = R.fma(r, P[4], P[3]), p56 = R.fma(r, P[6], P[5]);
  const q = R.fma(ar2, R.fma(ar2, p56, p34), p12);
  const lo = R.fma(ar3, q, lo4 + (lo3 + (lo1 + lo2)));
  const ly = hi + lo;
  const ltail = lo + (hi - ly);
  // y * log(x) as ehi + elo
  const ehi = y * ly;
  const elo = R.fma(y, ltail, prodErr(y, ly, ehi));          // fma(y, ltail, fma(y, ly, -ehi))
  PF[0] = ehi;
  const abstop = (PW[1] >>> 20) & 0x7ff;
  if (((abstop - 969) >>> 0) > 62) {
    if (abstop < 969) return ehi + 1.0;                      // |y log x| < 2^-54 (block 48, sign_bias 0)
    return pow_exact(x, y);
  }
  // exp_inline(ehi, elo, sign_bias = 0)
  const kd1 = R.fma(ehi, T_exp_data_invln2N, T_exp_data_shift);
  PF[0] = kd1;
  const kilo = PW[0];
  const kd2 = kd1 - T_exp_data_shift;
  const er = elo + R.fma(kd2, T_exp_data_negln2loN, R.fma(kd2, T_exp_data_negln2hiN, ehi));
  const idx = 2 * (kilo & 127);
  const EP = T_exp_data_poly;
  const r2 = er * er;
  const s1 = R.fma(r2, R.fma(er, EP[1], EP[0]), er + ETd[idx]);
  const tmp = R.fma(r2 * r2, R.fma(er, EP[3], EP[2]), s1);
  PW[0] = ETw[2 * idx + 2];
  PW[1] = ((kilo << 13) + ETw[2 * idx + 3]) | 0;
  const scale = PF[0];
  return R.fma(tmp, scale, scale);
}'''),
    ('ieee754_log10', '''const KS_NAN = R.u2f(0x7ff8000000000000n);   // __kernel_standard's NAN
export function log10(x: number): number {   // math/w_log10_compat.c
  if (x <= 0) { if (x === 0) return -Infinity; return KS_NAN; }
  return ieee754_log10(x);
}'''),
    ('ieee754_asin', '''export function asin(x: number): number {   // math/w_asin_compat.c
  if (Math.abs(x) > 1) return R.u2f(0x7ff8000000000000n);
  return ieee754_asin(x);
}'''),
    ('ieee754_acos', '''export function acos(x: number): number {   // math/w_acos_compat.c
  if (Math.abs(x) > 1) return R.u2f(0x7ff8000000000000n);
  return ieee754_acos(x);
}'''),
]
WRAPPERS_JAVA = [
    ('ieee754_log10', '''  static final double KS_NAN = Double.longBitsToDouble(0x7ff8000000000000L);   // __kernel_standard's NAN
  static double log10(double x) {   // math/w_log10_compat.c
    if (x <= 0) { if (x == 0) return Double.NEGATIVE_INFINITY; return KS_NAN; }
    return ieee754_log10(x);
  }'''),
    ('ieee754_asin', '''  static double asin(double x) {   // math/w_asin_compat.c
    if (Math.abs(x) > 1) return Double.longBitsToDouble(0x7ff8000000000000L);
    return ieee754_asin(x);
  }'''),
    ('ieee754_acos', '''  static double acos(double x) {   // math/w_acos_compat.c
    if (Math.abs(x) > 1) return Double.longBitsToDouble(0x7ff8000000000000L);
    return ieee754_acos(x);
  }'''),
]

WRAPPERS_GO = [
    ('ieee754_log10', """// Log10 is glibc's log10 symbol, the SVID wrapper math/w_log10_compat.c
func Log10(x float64) float64 {
	if x <= 0 {
		if x == 0 {
			return math.Inf(-1)
		}
		return ksNaN
	}
	return ieee754_log10(x)
}"""),
    ('ieee754_asin', """// Asin is glibc's asin symbol, the SVID wrapper math/w_asin_compat.c
func Asin(x float64) float64 {
	if math.Abs(x) > 1 {
		return ksNaN
	}
	return ieee754_asin(x)
}"""),
    ('ieee754_acos', """// Acos is glibc's acos symbol, the SVID wrapper math/w_acos_compat.c
func Acos(x float64) float64 {
	if math.Abs(x) > 1 {
		return ksNaN
	}
	return ieee754_acos(x)
}"""),
]


def main():
    args = sys.argv[1:]
    lang, outp = args[0], args[1]
    dumps, tables, rename, loop = [], {}, {}, False
    i = 2
    while i < len(args):
        if args[i] == '--tables':
            tables = json.load(open(args[i + 1]))
            i += 2
        elif args[i] == '--go-loop':
            loop = True
            i += 1
        elif args[i] == '--name':
            a, b = args[i + 1].split('=')
            rename[a] = b
            i += 2
        else:
            dumps.append(args[i])
            i += 1
    funcs = [f for d in dumps for f in parse(d)]
    sigs = {f.name: [k for k, _ in f.params] for f in funcs}
    sigs.update({'__math_oflow': ['u32'], '__math_uflow': ['u32'], '__math_may_uflow': ['u32'],
                 '__math_divzero': ['u32'], '__math_invalid': ['f64'], '__math_check_oflow': ['f64'],
                 '__math_check_uflow': ['f64'], '__branred': None, 'copysign': ['f64', 'f64']})
    rename['copysign'] = 'math.Copysign' if lang == 'go' else 'R.copysign'
    for k in ('oflow', 'uflow', 'may_uflow', 'divzero', 'invalid', 'check_oflow', 'check_uflow'):
        rename['__math_' + k] = ('math_' if lang == 'go' else 'R.math_') + k
    em = (GoEmitter if lang == 'go' else Emitter)(lang, tables, rename)
    em.loop = loop
    em.rets = {f.name: f.ret for f in funcs}
    em.rets.update({'__branred': 'i32', 'copysign': 'f64'})
    em.rets.update({'__math_' + k: 'f64' for k in ('oflow', 'uflow', 'may_uflow', 'divzero', 'invalid', 'check_oflow', 'check_uflow')})
    bodies = [em.function(f, sigs) for f in funcs]
    used = sorted(em.used_tables | ({'toverp[]'} if any('__branred' in s for f in funcs for st in f.blocks.values() for s in st) else set()))
    head = []
    for key in used:
        t = tables[key]
        name = 'T_' + re.sub(r'\W+', '_', key).strip('_')
        hexs = ''.join(t['data'])
        if lang == 'go':
            if not t['dims']:
                head.append('var %s = %s' % (name, ('math.Float64frombits(0x%s)' % hexs) if t['kind'] == 'f64' else ('uint64(0x%s)' % hexs)))
            else:
                head.append('var %s = %s("%s")' % (name, 'f64tab' if t['kind'] == 'f64' else 'u64tab', hexs))
        elif lang == 'ts':
            if not t['dims']:
                head.append('const %s = %s;' % (name, ('R.f64c("%s")' % hexs) if t['kind'] == 'f64' else ('0x%sn' % hexs)))
            else:
                head.append('const %s = %s("%s");' % (name, 'R.f64tab' if t['kind'] == 'f64' else 'R.u64tab', hexs))
        else:
            if not t['dims']:
                head.append('  static final %s %s = %s;' % ('double' if t['kind'] == 'f64' else 'long', name,
                            ('Double.longBitsToDouble(0x%sL)' % hexs) if t['kind'] == 'f64' else ('0x%sL' % hexs)))
            else:
                head.append('  static final %s[] %s = R.%s("%s");' % ('double' if t['kind'] == 'f64' else 'long', name,
                            'f64tab' if t['kind'] == 'f64' else 'u64tab', hexs))
    names = {rename.get(f.name, f.name) for f in funcs}
    tail = {'ts': WRAPPERS_TS, 'java': WRAPPERS_JAVA, 'go': WRAPPERS_GO}[lang]
    tail = '\n'.join(b for n, b in tail if n in names)
    bodies.append(tail)
    if lang == 'go':
        text = ('// Code generated by tools/libm/gim2x.py from GCC\'s optimized GIMPLE of glibc 2.39 (sysdeps/ieee754/dbl-64),\n'
                '// compiled as glibc compiles its x86-64 FMA variants. DO NOT EDIT; regenerate.\n\n'
                'package glibm\n\nimport "math"\n\n' + '\n'.join(head) + '\n\n' + '\n\n'.join(bodies) + '\n')
    elif lang == 'ts':
        text = ('// Generated by tools/libm/gim2x.py from GCC\'s optimized GIMPLE of glibc 2.39 (sysdeps/ieee754/dbl-64),\n'
                '// compiled as glibc compiles its x86-64 FMA variants. Do not edit; regenerate.\n'
                '/* eslint-disable */\n// @ts-nocheck\nimport * as R from "./glibm_rt";\n\n' + '\n'.join(head) + '\n\n' + '\n\n'.join(bodies) + '\n')
    else:
        cls = os.path.splitext(os.path.basename(outp))[0]
        text = ('// Generated by tools/libm/gim2x.py from GCC\'s optimized GIMPLE of glibc 2.39 (sysdeps/ieee754/dbl-64),\n'
                '// compiled as glibc compiles its x86-64 FMA variants. Do not edit; regenerate.\n'
                'package swmm5;\n\nfinal class %s {\n  private %s() {}\n  static final class R extends GlibmRt {}\n' % (cls, cls)
                + '\n'.join(head) + '\n\n' + '\n\n'.join(bodies) + '\n}\n')
    open(outp, 'w').write(text)
    print('translated', [rename.get(f.name, f.name) for f in funcs], 'tables', len(used))


if __name__ == '__main__':
    main()
