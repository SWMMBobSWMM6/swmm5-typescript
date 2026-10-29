// runswmm.ts - command-line runner for the TypeScript port: same arguments and console messages as
// EPA's run/main.c.  node dist/runswmm.js <input file> <report file> [output file]
// Written by Claude (Anthropic); see AI-USE.md.
import * as E from "./engine";
import * as R from "./crt";

function out(s: string): void { R.stdout.writeStr(s); R.stdout.flush(); }

function main(argv: string[]): number {
  const version = E.swmm_getVersion();
  const vMajor = Math.trunc(version / 10000);
  const vMinor = Math.trunc((version - 10000 * vMajor) / 1000);
  const vRelease = version - 10000 * vMajor - 1000 * vMinor;
  if (argv.length === 0) out("\nNot Enough Arguments (See Help --help)\n\n");
  else if (argv.length === 1) {
    const a = argv[0];
    if (a === "--help" || a === "-h") {
      out("\n\nSTORMWATER MANAGEMENT MODEL (SWMM) HELP\n\nCOMMANDS:\n\t--help (-h)       SWMM Help\n" +
          "\t--version (-v)    Build Version\n\nRUNNING A SIMULATION:\n" +
          "\t runswmm <input file> <report file> <optional output file>\n\n");
    } else if (a === "--version" || a === "-v") out(`\n${vMajor}.${vMinor}.${vRelease}\n\n`);
    else out("\nUnknown Argument (See Help --help)\n\n");
  } else {
    const start = Date.now();
    out(`\n... EPA SWMM ${vMajor}.${vMinor} (Build ${vMajor}.${vMinor}.${vRelease}) [TypeScript port]\n`);
    E.swmm_run(R.lit(argv[0]), R.lit(argv[1]), R.lit(argv.length > 2 ? argv[2] : ""));
    let msg = `\n\n... EPA SWMM completed in ${((Date.now() - start) / 1000).toFixed(2)} seconds.`;
    const err = R.buf(128);
    if (E.swmm_getError(err, 127) > 0) msg += " There are errors.\n";
    else if (E.swmm_getWarnings() > 0) msg += " There are warnings.\n";
    else msg += "\n";
    out(msg);
  }
  return 0;
}

process.exitCode = main(process.argv.slice(2));
