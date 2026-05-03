{
  description = "delete-me-discord CLI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    let
      project = builtins.fromTOML (builtins.readFile ./pyproject.toml);
      pname = project.project.name;
      version = project.project.version;
    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;
        pythonPackages = pkgs.python312Packages;

        delete-me-discord = pythonPackages.buildPythonApplication {
          inherit pname version;
          pyproject = true;
          src = ./.;

          build-system = with pythonPackages; [
            setuptools
          ];

          dependencies = with pythonPackages; [
            keyring
            requests
            rich
          ];

          pythonImportsCheck = [
            "delete_me_discord"
          ];

          meta = with pkgs.lib; {
            description = project.project.description;
            homepage = project.project.urls.homepage;
            license = licenses.mit;
            mainProgram = "delete-me-discord";
            platforms = platforms.unix ++ platforms.windows;
          };
        };
      in
      {
        packages = {
          default = delete-me-discord;
          delete-me-discord = delete-me-discord;
        };

        apps = {
          default = {
            type = "app";
            program = "${delete-me-discord}/bin/delete-me-discord";
          };
        };

        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps: [
              ps.pip
              ps.requests
              ps.rich
              ps.pytest
              ps.pyinstaller
              ps.packaging
              ps.pytest-cov
            ]))
            pkgs.pre-commit
            pkgs.nodejs_22
            pkgs.pnpm
          ];

          shellHook = ''
            unset PYTHONPATH
            if [ ! -d .venv ]; then
              python -m venv .venv
            fi
            source .venv/bin/activate
            export PIP_REQUIRE_VIRTUALENV=1
            export PNPM_HOME="$PWD/.pnpm-home"
            export PATH="$PNPM_HOME:$PATH"
          '';
        };
      });
}
