{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312.withPackages (ps: [
    ps.pip
    ps.requests
    ps.rich
    ps.pytest
    ps.pyinstaller
    ps.packaging
    ps.pytest-cov
  ]);
in
pkgs.mkShell {
  buildInputs = [
    python
    pkgs.pre-commit
  ];
  shellHook = ''
    if [ ! -d .venv ]; then
      python -m venv .venv
    fi
    # Keep installs writable and isolated from the Nix store.
    source .venv/bin/activate
    export PIP_REQUIRE_VIRTUALENV=1
  '';
}
