# The programs the aiprover-captain skill and the AIProver CLI call, as one profile. ./setup
# links it at aiprover/nix/tools, and the skill's env.sh puts its bin/ first on PATH, so a
# Claude Code session started outside the Nix shell uses exactly these. Lean is not here: elan
# installs the toolchain into ~/.elan, where the AIProver harness expects it.
{ pkgs }:
pkgs.buildEnv {
  name = "aiprover-orchestration-tools";
  paths = with pkgs; [
    # The skill's helpers, the trace pages (pygments) and the reasoning proxy (aiohttp).
    (python312.withPackages (ps: [
      ps.pygments
      ps.aiohttp
    ]))
    jq
    ripgrep
    git
    curl
    openssh
    # GNU userland on macOS as on Linux (readlink -f, date, sed, find -quit, bash 5 for the
    # setup scripts); flock guards the Lake workspace.
    bash
    coreutils
    findutils
    gnused
    flock
    # pdflatex with the packages of PREAMBLE in aiprover_orchestration/orchestrator/reports.
    (texliveSmall.withPackages (
      ps: with ps; [
        amsmath
        booktabs
        cleveref
        enumitem
        fancyvrb
        fvextra
        geometry
        hyperref
        inconsolata
        lineno
        lm
        mathtools
        microtype
        needspace
        tools
        upquote
        xcolor
      ]
    ))
  ];
}
