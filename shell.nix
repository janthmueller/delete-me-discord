
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell rec {
  buildInputs = with pkgs; [
    python312
    python312Packages.pip
    pre-commit
  ];

  shellHook = ''

    # Persistent virtualenv
    if [ ! -d ".venv" ]; then
        python -m venv .venv --system-site-packages
    fi
    source .venv/bin/activate

    # Ensure commit-msg hook is installed
    if command -v pre-commit >/dev/null 2>&1; then
      pre-commit install --hook-type commit-msg >/dev/null 2>&1 || true
    fi
  '';
}
