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

            # -- SMT solvers (for Veil / lean-smt) --
            z3
            cvc5

            # -- Git (experiment tracking) --
            git

            # -- Dev tools --
            jq
            ripgrep
            tmux        # for overnight runs
          ];

          shellHook = ''
            echo "AutoLean dev shell"
            echo "  lean:   $(lean --version 2>/dev/null || echo 'run: elan default stable')"
            echo "  uv:     $(uv --version)"
            echo "  z3:     $(z3 --version 2>/dev/null || echo 'not found')"
            echo "  cvc5:   $(cvc5 --version 2>/dev/null || echo 'not found')"
            echo "  ollama: $(ollama --version 2>/dev/null || echo 'not running')"
            echo ""
            echo "Commands:"
            echo "  uv run autolean prove \"1 + 1 = 2\""
            echo "  uv run autolean run --overnight"
            echo "  uv run autolean verify <arxiv-url>"
            echo "  uv run autolean build-library \"group theory\""
          '';

          UV_PYTHON = "${pkgs.python312}/bin/python3";
        };

        packages.default = pkgs.writeShellApplication {
          name = "autolean";
          runtimeInputs = with pkgs; [ python312 uv elan git z3 cvc5 ];
          text = ''
            cd "$(dirname "$(realpath "$0")")/.." || exit 1
            exec uv run autolean "$@"
          '';
        };
      }
    );
}
