
{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312.withPackages (ps: [
    ps.pip
    ps.requests
    ps.rich
    ps.pytest
  ]);
in
pkgs.mkShell {
  buildInputs = [
    python
    pkgs.pre-commit
  ];

  shellHook = ''

    # Persistent virtualenv using the nix-provided Python (with deps baked in)
    if [ ! -d ".venv" ]; then
        ${python}/bin/python -m venv .venv --system-site-packages
    fi
    source .venv/bin/activate

    # Ensure commit-msg hook is installed
    if command -v pre-commit >/dev/null 2>&1; then
      pre-commit install --hook-type commit-msg >/dev/null 2>&1 || true
    fi
  '';
}
