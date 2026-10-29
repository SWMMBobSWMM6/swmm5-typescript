"""census_summary.py DIR : totals of libm calls per function over the census files, and models using each."""
import os, struct, sys, collections
NARGS = {'pow': 2}
tot = collections.Counter(); models = collections.Counter(); top = collections.defaultdict(list)
for fn in sorted(os.listdir(sys.argv[1])):
    b = open(os.path.join(sys.argv[1], fn), 'rb').read(); i = 0
    while i < len(b):
        j = b.index(b'\0', i); name = b[i:j].decode(); i = j + 1
        count, n = struct.unpack_from('<QI', b, i); i += 12
        i += n * 8 * NARGS.get(name, 1)
        tot[name] += count
        if count: models[name] += 1; top[name].append((count, fn[:-4]))
for k in sorted(tot, key=lambda k: -tot[k]):
    t = sorted(top[k], reverse=True)[:3]
    print('%-6s %15s calls in %3d models   biggest: %s' % (k, format(tot[k], ','), models[k], ', '.join('%s %s' % (format(c, ','), m) for c, m in t)))
