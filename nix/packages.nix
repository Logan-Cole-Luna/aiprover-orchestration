# The packages of flake.nix: the skill's tools and the AIProver harness virtualenvs. The central
# AIProver repository imports this file as well, so both flakes build the same derivations.
{
  pkgs,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
}:
let
  virtualenv = import ./venvs.nix {
    inherit
      pkgs
      pyproject-nix
      uv2nix
      pyproject-build-systems
      ;
  };
  mcpPatch = name: ./python/mcp/patches/${name}.patch;
in
{
  tools = import ./tools.nix { inherit pkgs; };
  venv-vibe = virtualenv {
    name = "vibe";
    project = ./python/vibe;
  };
  # The three lean-lsp-mcp fixes of AIProver/AIProver_plugin/setup.sh (step_venvs).
  venv-mcp = virtualenv {
    name = "mcp";
    project = ./python/mcp;
    patches = {
      leanclient = [
        (mcpPatch "leanclient-min-lean-version")
        (mcpPatch "leanclient-scratch-warm-text")
      ];
      lean-lsp-mcp = [
        (mcpPatch "lean-lsp-mcp-scratch-warm-text")
        (mcpPatch "lean-lsp-mcp-search-follow")
      ];
    };
  };
}
