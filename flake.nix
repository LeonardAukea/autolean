{
  description = "AutoLean — Autonomous Lean 4 proof agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachSystem [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ] (
      system: let
        pkgs = import nixpkgs {inherit system;};
        lib = pkgs.lib;
        pythonPackages = pkgs.python312Packages;
        leanVersion = "4.33.0";
        leanAsset = builtins.getAttr system {
          aarch64-darwin = {
            platform = "darwin_aarch64";
            hash = "sha256-21J0tmm+JwrwSLXk8eDOVx32dQ5BGVaz4eb8wgEkEMI=";
          };
          aarch64-linux = {
            platform = "linux_aarch64";
            hash = "sha256-+WGkF8uhC26gqdE2cS1ZUoE4F//WaABB8JojNSb4A6k=";
          };
          x86_64-linux = {
            platform = "linux";
            hash = "sha256-Sz+wPCmh4KJT+x0R+brjcl8ZoNxvwJs+oW0snfM0niw=";
          };
        };
        leanArchive = "lean-${leanVersion}-${leanAsset.platform}.tar.zst";
        lean4Pinned = pkgs.stdenv.mkDerivation {
          pname = "lean4";
          version = leanVersion;
          src = pkgs.fetchurl {
            url = "https://github.com/leanprover/lean4/releases/download/v${leanVersion}/${leanArchive}";
            inherit (leanAsset) hash;
          };
          sourceRoot = lib.removeSuffix ".tar.zst" leanArchive;
          nativeBuildInputs =
            [pkgs.zstd]
            ++ lib.optionals pkgs.stdenv.isLinux [pkgs.autoPatchelfHook];
          buildInputs = lib.optionals pkgs.stdenv.isLinux [
            pkgs.glibc
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];
          dontConfigure = true;
          dontBuild = true;
          dontStrip = true;
          installPhase = ''
            runHook preInstall
            mkdir -p "$out"
            cp -R . "$out"
            runHook postInstall
          '';
          meta = {
            description = "Lean theorem prover ${leanVersion}";
            homepage = "https://lean-lang.org/";
            license = lib.licenses.asl20;
            mainProgram = "lean";
          };
        };
        click = pythonPackages.click.overridePythonAttrs {
          version = "8.4.2";
          src = pkgs.fetchPypi {
            pname = "click";
            version = "8.4.2";
            hash = "sha256-mmzqbmCxfr4KRMXMY22U8JvWYULBzX2LTNcxxJF6FfY=";
          };
        };
        treeSitterAsset = builtins.getAttr system {
          aarch64-darwin = {
            path = "54/6f/8bb61957f16ec1b1d92410a006cdc84a952b6352a7313b2ad299f2d21484/tree_sitter-0.26.0-cp312-cp312-macosx_11_0_arm64.whl";
            hash = "sha256-kY2JUpeGhz8JgqD1nCowPNBl+/0bkD1xqOThWE9ntC4=";
          };
          aarch64-linux = {
            path = "78/0a/8a6f08559182643a814a4ab559948ae817b2851890fd9b995a4fff6541ce/tree_sitter-0.26.0-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl";
            hash = "sha256-MKiL6J/x8nVSl/gegIDYi3ld2Ycgw/n6Ks+ThzGCzJU=";
          };
          x86_64-linux = {
            path = "8a/2f/6e6781b31677231366cb3cf27bc8269157f6d4b03c9032865a4f5f2bbe7e/tree_sitter-0.26.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl";
            hash = "sha256-WmszOwKC2LsK90H5sBi9JSPU7ssmhr9nFwZqYl/s+qQ=";
          };
        };
        treeSitter = pythonPackages.buildPythonPackage {
          pname = "tree-sitter";
          version = "0.26.0";
          format = "wheel";
          src = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/${treeSitterAsset.path}";
            inherit (treeSitterAsset) hash;
          };
          nativeBuildInputs = lib.optionals pkgs.stdenv.isLinux [pkgs.autoPatchelfHook];
          doCheck = false;
          pythonImportsCheck = ["tree_sitter"];
        };
        languagePackAsset = builtins.getAttr system {
          aarch64-darwin = {
            path = "48/bc/ff15c101fb874f8c80f0f722420915884a1233ddae3eff920f56a5b16b76/tree_sitter_language_pack-1.14.3-cp310-abi3-macosx_11_0_arm64.whl";
            hash = "sha256-O1YCi7pHK3dM3QDu3SdEoeZjoAPFIsGAcWQvJQEBYhI=";
          };
          aarch64-linux = {
            path = "fa/93/fe18a7da47f9189c75584c6996568d6052f0c9b5b29831cc49f213c8b7e0/tree_sitter_language_pack-1.14.3-cp310-abi3-manylinux_2_34_aarch64.whl";
            hash = "sha256-sTcm7SxAIDXtG2AQgKNZejNG3Ul2T0uS0JxvWh19EhQ=";
          };
          x86_64-linux = {
            path = "bf/5c/70fa8a6d2bcc2578d38e1853382591134584eea8fa9556477c533d793f0c/tree_sitter_language_pack-1.14.3-cp310-abi3-manylinux_2_34_x86_64.whl";
            hash = "sha256-yd+FRd9I7hF0T5+9HcyXKicN+epIUyHOKeNS3WTLGkw=";
          };
        };
        treeSitterLanguagePack = pythonPackages.buildPythonPackage {
          pname = "tree-sitter-language-pack";
          version = "1.14.3";
          format = "wheel";
          src = pkgs.fetchurl {
            url = "https://files.pythonhosted.org/packages/${languagePackAsset.path}";
            inherit (languagePackAsset) hash;
          };
          dependencies = [treeSitter];
          nativeBuildInputs = lib.optionals pkgs.stdenv.isLinux [pkgs.autoPatchelfHook];
          doCheck = false;
          pythonImportsCheck = ["tree_sitter_language_pack"];
        };
        grammarBundleAsset = builtins.getAttr system {
          aarch64-darwin = {
            platform = "macos-arm64";
            extension = "dylib";
            hash = "sha256-cJf3FdB2iObBJ0CQjHEuZ9VnKusFlx3sO2XRnPcIAVk=";
          };
          aarch64-linux = {
            platform = "linux-aarch64";
            extension = "so";
            hash = "sha256-DsYUy02sElUc175Cc7bFYaNC+s5ptQ4bSG2wuvcmDqI=";
          };
          x86_64-linux = {
            platform = "linux-x86_64";
            extension = "so";
            hash = "sha256-k1wJkPCM3p9B/1UZ3lEptrc6zrzICm22R6Gq31yhmnc=";
          };
        };
        grammarBundle = pkgs.fetchurl {
          url = "https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v1.14.3/parsers-${grammarBundleAsset.platform}.tar.zst";
          inherit (grammarBundleAsset) hash;
        };
        leanGrammar =
          pkgs.runCommand "tree-sitter-lean-1.14.3" {
            nativeBuildInputs = [pkgs.gnutar pkgs.zstd];
          } ''
            mkdir -p "$out"
            tar --zstd -xf ${grammarBundle} -C "$out" \
              ./libtree_sitter_lean.${grammarBundleAsset.extension}
          '';
        leanGrammarLibrary = "${leanGrammar}/libtree_sitter_lean.${grammarBundleAsset.extension}";
        source = lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./autolean
            ./tests
            ./README.md
            ./pyproject.toml
          ];
        };
        runtimeTools =
          [lean4Pinned pkgs.git]
          ++ lib.optionals pkgs.stdenv.isLinux [pkgs.bubblewrap];
        autolean = pythonPackages.buildPythonApplication {
          pname = "autolean";
          version = "0.5.0";
          pyproject = true;
          src = source;

          build-system = [pythonPackages.hatchling];
          dependencies =
            (with pythonPackages; [
              beautifulsoup4
              httpx
              pyyaml
              rich
              textual
            ])
            ++ [
              click
              treeSitter
              treeSitterLanguagePack
            ];

          nativeBuildInputs = [pkgs.makeWrapper];
          doCheck = false;
          postFixup = ''
            wrapProgram "$out/bin/autolean" \
              --prefix PATH : "${lib.makeBinPath runtimeTools}" \
              --set AUTOLEAN_TREE_SITTER_LEAN_LIBRARY "${leanGrammarLibrary}"
          '';
        };
        sandboxTestPython = pkgs.python312.withPackages (ps: [
          ps.pytest
          ps.rich
        ]);
        structureTestPython = pkgs.python312.withPackages (_: [
          treeSitter
          treeSitterLanguagePack
        ]);
        structureTest = pkgs.runCommand "autolean-lean-structure" {} ''
          AUTOLEAN_TREE_SITTER_LEAN_LIBRARY=${leanGrammarLibrary} \
            PYTHONPATH=${source} \
            ${structureTestPython}/bin/python -c \
            'from pathlib import Path; from autolean.structure import LeanStructureProvider; source = "theorem smoke : True := by\\n  sorry\\n"; context = LeanStructureProvider().inspect(Path("Smoke.lean"), source, line=2, col=3, declaration_name="smoke"); assert context.target is not None; assert context.target.name == "smoke"; assert "grammar-sha256/" in context.parser'
          touch "$out"
        '';
        sandboxTest =
          pkgs.runCommand "autolean-generated-code-sandbox" {
            nativeBuildInputs = [
              pkgs.bubblewrap
              pkgs.curl
              lean4Pinned
              sandboxTestPython
            ];
          } ''
            mkdir -p "$TMPDIR/project/AutoLean"
            echo '-- sandbox project' > "$TMPDIR/project/lakefile.lean"
            printf 'example : True := by\n  sorry\n' \
              > "$TMPDIR/project/AutoLean/Target.lean"
            cd ${source}
            AUTOLEAN_RUN_SANDBOX_E2E=1 \
              AUTOLEAN_SANDBOX_PROJECT="$TMPDIR/project" \
              PYTHONPATH=${source} \
              ${sandboxTestPython}/bin/python -m pytest -q \
                -p no:cacheprovider \
                tests/test_lean_sandbox_e2e.py
            touch "$out"
          '';
        sandboxVmTest = pkgs.testers.runNixOSTest {
          name = "autolean-generated-code-sandbox";
          nodes.machine = {pkgs, ...}: {
            documentation.enable = false;
            environment.systemPackages = [
              pkgs.bubblewrap
              pkgs.curl
              lean4Pinned
              sandboxTestPython
            ];
            virtualisation.memorySize = 2048;
          };
          testScript = ''
            start_all()
            machine.succeed("mkdir -p /tmp/autolean-project/AutoLean")
            machine.succeed("echo '-- sandbox project' > /tmp/autolean-project/lakefile.lean")
            machine.succeed("printf 'example : True := by\\n  sorry\\n' > /tmp/autolean-project/AutoLean/Target.lean")
            machine.succeed(
                "cd ${source} && "
                "AUTOLEAN_RUN_SANDBOX_E2E=1 "
                "AUTOLEAN_SANDBOX_PROJECT=/tmp/autolean-project "
                "PYTHONPATH=${source} "
                "${sandboxTestPython}/bin/python -m pytest -q "
                "-p no:cacheprovider "
                "tests/test_lean_sandbox_e2e.py"
            )
          '';
        };
      in {
        packages =
          {
            default = autolean;
            lean = lean4Pinned;
          }
          // lib.optionalAttrs pkgs.stdenv.isLinux {
            generated-code-sandbox-vm = sandboxVmTest;
          };

        devShells.default = pkgs.mkShell {
          name = "autolean";
          packages =
            (with pkgs; [
              cvc5
              curl
              jq
              ollama
              python312
              ripgrep
              tmux
              tesseract
              uv
              z3
              zstd
            ])
            ++ runtimeTools
            ++ [autolean];

          shellHook = ''
            first_line() {
              "$@" 2>/dev/null | head -n 1
            }
            echo "AutoLean dev shell"
            printf '  %-9s %s\n' autolean "$(first_line autolean --version)"
            printf '  %-9s %s\n' lean "$(first_line lean --version)"
            printf '  %-9s %s\n' uv "$(first_line uv --version)"
            printf '  %-9s %s\n' z3 "$(first_line z3 --version)"
            printf '  %-9s %s\n' cvc5 "$(first_line cvc5 --version)"
            printf '  %-9s %s\n' ollama "$(first_line ollama --version)"
            echo
            echo "Commands:"
            echo "  autolean workbench"
            echo "  autolean doctor"
            echo "  uv sync --all-extras --all-groups"
            echo "  uv run autolean solve --overnight"
          '';

          UV_PYTHON = "${pkgs.python312}/bin/python3";
        };

        checks =
          {
            lean-version = pkgs.runCommand "autolean-lean-version" {} ''
              actual="$(${lean4Pinned}/bin/lean --version)"
              case "$actual" in
                "Lean (version ${leanVersion},"*) ;;
                *)
                  echo "expected Lean ${leanVersion}, got: $actual" >&2
                  exit 1
                  ;;
              esac
              touch "$out"
            '';
            lean-structure = structureTest;
          }
          // lib.optionalAttrs pkgs.stdenv.isLinux {
            generated-code-sandbox = sandboxTest;
          };

        formatter = pkgs.alejandra;
      }
    );
}
