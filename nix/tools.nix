# The programs the aiprover-captain skill and the AIProver CLI call, as one profile. ./setup
# links it at aiprover/nix/tools, and the skill's env.sh puts its bin/ first on PATH, so a
# Claude Code session started outside the Nix shell uses exactly these. Lean is not here: elan
# installs the toolchain into ~/.elan, where the AIProver harness expects it.
{ pkgs }:
pkgs.buildEnv {
  name = "aiprover-orchestration-tools";
  paths = with pkgs; [
    # The skill's helpers, the trace pages (pygments), the reasoning proxy (aiohttp), and the
    # query server and pipeline (fastapi, uvicorn, anthropic, psutil).
    (python312.withPackages (ps: [
      ps.pygments
      ps.aiohttp
      ps.fastapi
      ps.uvicorn
      ps.anthropic
      ps.psutil
    ]))
    jq
    ripgrep
    git
    curl
    # No ssh: the system's own client is used, with the user's ~/.ssh/config, agent and
    # platform options (Ubuntu's permission rules, GSSAPI, macOS UseKeychain), which an
    # unpatched Nix OpenSSH rejects.
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
