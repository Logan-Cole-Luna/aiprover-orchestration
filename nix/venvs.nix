# A virtualenv of one uv project (pyproject.toml and uv.lock) under Python 3.12, built by
# uv2nix from wheels only. `patches` maps a package name to patch files applied to its
# installed sources (paths relative to site-packages, `patch -p1`). The cached bytecode of each
# patched module is removed: the store gives every file the same mtime, so a patch that keeps
# a file's size would otherwise leave Python running the unpatched .pyc.
{
  pkgs,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
}:
{
  name,
  project,
  patches ? { },
}:
let
  inherit (pkgs) lib;
  python = pkgs.python312;
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = project; };
  applyPatches =
    files:
    lib.concatMapStrings (patch: ''
      patch -p1 < ${patch}
      for source in $(sed -n 's|^+++ b/||p' ${patch}); do
        module=$(basename "$source" .py)
        rm -f "$(dirname "$source")/__pycache__/$module".*.pyc
      done
    '') files;
  patchOverlay =
    _final: prev:
    lib.mapAttrs (
      package: files:
      prev.${package}.overrideAttrs (old: {
        postInstall = (old.postInstall or "") + ''
          pushd "$out/${python.sitePackages}" > /dev/null
          ${applyPatches files}
          popd > /dev/null
        '';
      })
    ) patches;
  pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.default
      (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
      patchOverlay
    ]
  );
in
pythonSet.mkVirtualEnv "aiprover-${name}-env" workspace.deps.default
