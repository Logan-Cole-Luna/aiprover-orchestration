#!/bin/bash
# Pack directories into tar shards, and extract them.
#
#   pack.sh DIR...         writes DIR.pack/part*.tar and DIR.pack/all.list
#   pack.sh -x PACK DEST   extracts PACK into the directory DEST
#
# Shared file systems serve many small files slowly and a few large files
# quickly, so a job extracts a pack to node-local disk and works there. A pack
# lies beside its directory, or in AIPROVER_PACK_ROOT if set. Packing is
# compute-heavy; submit it as a job: sbatch --wrap 'scripts/slurm/pack.sh DIR'.
set -euo pipefail
shards=${PACK_SHARDS:-16}

if [ "${1:-}" = -x ]; then
  pack=$2 dest=$3
  mkdir -p "$dest"
  # Parallel tars that create a shared parent directory race, so the
  # directories come first.
  (cd "$dest" && sed 's|/[^/]*$||' "$pack/all.list" | sort -u | xargs -d '\n' mkdir -p)
  ls "$pack"/part*.tar | xargs -P "$shards" -I{} tar -C "$dest" -xf {}
  exit
fi

for source in "$@"; do
  source=$(readlink -f "$source")
  parent=$(dirname "$source") leaf=$(basename "$source")
  out=${AIPROVER_PACK_ROOT:-$parent}/$leaf.pack
  rm -rf "$out.tmp" && mkdir -p "$out.tmp"
  # lfs find reads the Lustre metadata server, which is far faster than find.
  (cd "$parent" && { lfs find "$leaf" 2>/dev/null || find "$leaf"; }) > "$out.tmp/all.list"
  awk -v n="$shards" -v d="$out.tmp" '{ print > sprintf("%s/part%02d.list", d, NR % n) }' "$out.tmp/all.list"
  ls "$out.tmp"/part*.list | xargs -P "$shards" -I{} sh -c \
    'tar -C "$1" --no-recursion -cf "${2%.list}.tar" -T "$2"' _ "$parent" {}
  rm "$out.tmp"/part*.list
  packed=0
  for shard in "$out.tmp"/part*.tar; do packed=$((packed + $(tar -tf "$shard" | wc -l))); done
  [ "$packed" -eq "$(wc -l < "$out.tmp/all.list")" ] || { echo "$source: incomplete pack" >&2; exit 1; }
  rm -rf "$out" && mv "$out.tmp" "$out"
  echo "packed $source: $(wc -l < "$out/all.list") entries, $(du -sh "$out" | cut -f1)"
done
