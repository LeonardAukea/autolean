{
  description = "AutoLean — Autonomous Lean 4 proof agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          name = "autolean";

          buildInputs = with pkgs; [
            # -- Lean 4 (via elan) --
            elan

            # -- Python (orchestration) --
            python312
            uv

            # -- Ollama (local LLM) --
            ollama

            # -- Git (experiment tracking) --
            git

            # -- Dev tools --
            jq          # JSON processing for Ollama responses
            ripgrep     # Fast sorry scanning fallback
          ];

          shellHook = ''
            echo "🔧 AutoLean dev shell"
            echo "  lean: $(lean --version 2>/dev/null || echo 'run: elan default stable')"
            echo "  uv:   $(uv --version)"
            echo "  ollama: $(ollama --version 2>/dev/null || echo 'not running')"
            echo ""
            echo "Quick start:"
            echo "  uv sync                    # install Python deps"
            echo "  ollama serve &             # start Ollama (if not running)"
            echo "  uv run autolean            # run the agent"
            echo "  uv run autolean --help     # see options"
          '';

          # Ensure uv uses the Nix Python
          UV_PYTHON = "${pkgs.python312}/bin/python3";
        };

        # -- Package (for `nix run .`) --
        packages.default = pkgs.writeShellApplication {
          name = "autolean";
          runtimeInputs = with pkgs; [ python312 uv elan git ];
          text = ''
            cd "$(dirname "$(realpath "$0")")/.." || exit 1
            exec uv run autolean "$@"
          '';
        };
      }
    );
}
