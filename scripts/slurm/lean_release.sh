#!/bin/bash
# Install a Lean release, with the Mathlib, REPL, and cslib that fit it, for runs
# on a Slurm cluster (--lean-release RELEASE of the batch command).
#
#   lean_release.sh list             the releases that all packages support
#   lean_release.sh RELEASE|LINK     RELEASE is v4.28.0, for example, and LINK any
#                                    URL that contains one, such as a release page
#
# Compute nodes have no network, so the downloads run here, and CPU jobs build
# the release and pack it (pack.sh) for the nodes. A login node runs nothing heavy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
. "$ROOT/scripts/slurm/env.sh"
releases="aiprover_orchestration.lean.releases"
[ $# -eq 1 ] || { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ "$1" != list ] || exec python -m "$releases" list

release=$(python -c 'import sys; from aiprover_orchestration.lean.releases import release_of; print(release_of(sys.argv[1]))' "$1")
project=$(python -c 'import sys; from aiprover_orchestration.lean.releases import project_path; print(project_path(sys.argv[1]))' "$release")
# Releases take about 16 GB, which suits scratch rather than a home directory.
if [ ! -e lean_releases ] && [ -n "${SCRATCH:-}" ]; then
  mkdir -p "$SCRATCH/aiprover_lean_releases" && ln -s "$SCRATCH/aiprover_lean_releases" lean_releases
fi
mkdir -p logs
python -m "$releases" "$release" fetch
job() {  # job NAME CPUS MEM TIME DEPENDENCY COMMAND
  sbatch --parsable --job-name="aiprover_$1" --cpus-per-task="$2" --mem="$3" --time="$4" \
    ${5:+--dependency="afterok:$5"} --output="$ROOT/logs/%x_%j.out" --wrap "$6" | cut -d';' -f1
}
build=$(job "build_$release" 8 32G 01:30:00 "" "cd '$ROOT' && python -m $releases $release build")
pack=$(job "pack_$release" 16 16G 02:00:00 "$build" "bash scripts/slurm/pack.sh '$project'")
echo "build job $build, then pack job $pack; afterwards submit with --lean-release $release"
