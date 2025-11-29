
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell rec {
  buildInputs = with pkgs; [
    python312
    python312Packages.pip
  ];

  shellHook = ''

    # Persistent virtualenv
    if [ ! -d ".venv" ]; then
        python -m venv .venv --system-site-packages
    fi
    source .venv/bin/activate
  '';
}

