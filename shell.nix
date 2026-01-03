{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312.withPackages (ps: [
    ps.pip
    ps.requests
    ps.rich
    ps.pytest
    ps.pyinstaller
    ps.packaging
  ]);
in
pkgs.mkShell {
  buildInputs = [
    python
    pkgs.pre-commit
  ];
}
