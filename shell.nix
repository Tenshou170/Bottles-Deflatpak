{
  pkgs ? import <nixpkgs> { },
}:

let
  version = pkgs.lib.removeSuffix "\n" (builtins.readFile ./VERSION);
  # Filter local build artifacts out of the source: a raw ./. would copy
  # gitignored dirs (build/, venv/, ...) into the store, and a stale meson
  # build/ breaks the sandbox build.
  src = pkgs.lib.cleanSourceWith {
    src = ./.;
    filter =
      path: type:
      let
        base = baseNameOf path;
      in
      !(builtins.elem base [
        "build"
        "builddir"
        "build-dir"
        "venv"
        ".venv"
        "result"
        ".flatpak-builder"
        "flatpak"
        "repo"
        "mesonbuild"
        ".mypy_cache"
        ".pytest_cache"
        ".pre-commit-cache"
      ]);
  };

  bottles-deflatpak-unwrapped = pkgs.callPackage ./nix/package.nix {
    inherit version src;
    inherit (pkgs) file kmod;
    obs-vkcapture = pkgs.obs-studio-plugins.obs-vkcapture;
  };
in
pkgs.mkShell {
  name = "bottles-deflatpak-dev";
  inputsFrom = [ bottles-deflatpak-unwrapped ];
  nativeBuildInputs = with pkgs; [
    meson
    ninja
    blueprint-compiler
    pkg-config
    python3
    wrapGAppsHook4
    gtk4
    libadwaita
    appstream-glib
    desktop-file-utils
    dbus
    xvfb
    xvfb-run
  ];
}
