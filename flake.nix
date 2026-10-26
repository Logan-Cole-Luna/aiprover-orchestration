{
  description = "AIProver orchestration: tools of the aiprover-captain skill and the AIProver harness virtualenvs";

  inputs = {
    # The darwin-stable channel, as in the flywheel superproject; it serves Linux as well.
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-26.05-darwin";
    # uv2nix: each uv.lock under nix/python/ built into a virtualenv.
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      # Windows is WSL2 running the x86_64-linux packages.
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      # ./setup links each of these into aiprover/nix/ (nix build --out-link).
      packages = forAllSystems (
        system:
        let
          packages = import ./nix/packages.nix {
            pkgs = nixpkgs.legacyPackages.${system};
            inherit pyproject-nix uv2nix pyproject-build-systems;
          };
        in
        packages // { default = packages.tools; }
      );

      # `nix develop`, or `direnv allow` once: the same tools for interactive work.
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [ self.packages.${system}.tools ];
            shellHook = ''
              export AIPROVER_ORCHESTRATION_ROOT="$PWD"
              echo "aiprover orchestration: run ./setup once; then ask Claude Code for an aiprover-captain run"
            '';
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
