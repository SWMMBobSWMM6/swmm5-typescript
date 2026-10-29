#!/bin/bash
# run_census.sh OUTDIR : run EPA on every gate model under the libm census interposer (2 at a time)
cd "$(dirname "$0")/../.."
OUT=$1; mkdir -p "$OUT"
CORPUS=${SWMM_CORPUS:-$(pwd)/../1729-SWMM5-Models-2030}
while IFS=$'\t' read -r id path; do
  [[ "$path" = /* ]] || path="$CORPUS/$path"; printf '%s\t%s\n' "$id" "$path"
done < gate/models_all.tsv | xargs -P 2 -d '\n' -I{} bash -c '
  id=$(echo "{}" | cut -f1); p=$(echo "{}" | cut -f2-); t=$(mktemp -d)
  ( cd "$(dirname "$p")" && timeout 1200 env LD_PRELOAD='"$PWD"'/tools/libm/census.so LIBM_CENSUS='"$OUT"'/$id.bin '"$PWD"'/ref/runswmm "$(basename "$p")" $t/r.rpt $t/r.out >/dev/null 2>&1 )
  rm -rf $t'
echo done $(ls "$OUT" | wc -l)
