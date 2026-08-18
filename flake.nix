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
        lightpandaVersion = "0.3.6";
        lightpandaAsset = builtins.getAttr system {
          aarch64-darwin = {
            name = "lightpanda-aarch64-macos";
            hash = "sha256-M1aJNNN02vkBK5vghH/YKpndnxyy8vk7t4PMeKlsmaw=";
          };
          aarch64-linux = {
            name = "lightpanda-aarch64-linux";
            hash = "sha256-KcBZzQdVoZU1DMedvPfulYD9V17D6qMdt1XbraQX5hY=";
          };
          x86_64-linux = {
            name = "lightpanda-x86_64-linux";
            hash = "sha256-5DjArUTg9pFsFM8TvrADUSxgQ42P0gBzjS5ZbnP2UtY=";
          };
        };
        lightpanda = pkgs.stdenv.mkDerivation {
          pname = "lightpanda";
          version = lightpandaVersion;
          src = pkgs.fetchurl {
            url = "https://github.com/lightpanda-io/browser/releases/download/${lightpandaVersion}/${lightpandaAsset.name}";
            inherit (lightpandaAsset) hash;
          };
          nativeBuildInputs = lib.optionals pkgs.stdenv.isLinux [pkgs.autoPatchelfHook];
          buildInputs = lib.optionals pkgs.stdenv.isLinux [
            pkgs.glibc
            pkgs.stdenv.cc.cc.lib
          ];
          dontUnpack = true;
          dontConfigure = true;
          dontBuild = true;
          installPhase = ''
            runHook preInstall
            install -Dm755 "$src" "$out/bin/lightpanda"
            runHook postInstall
          '';
          meta = {
            description = "Headless browser for AI and automation";
            homepage = "https://lightpanda.io/";
            license = lib.licenses.agpl3Only;
            mainProgram = "lightpanda";
          };
        };
        codedbVersion = "0.2.5838";
        codedbAsset = builtins.getAttr system {
          aarch64-darwin = {
            name = "codedb-darwin-arm64";
            hash = "sha256-W6BULl8rsdUBZaM/giSFlEuj+cCZR0IwtX5xOQT7mEc=";
          };
          aarch64-linux = {
            name = "codedb-linux-arm64";
            hash = "sha256-tp4n3Pxqm3mmdD7wfR4uOjHNcR1qa6MceJOij5lIdek=";
          };
          x86_64-linux = {
            name = "codedb-linux-x86_64";
            hash = "sha256-2/Lk9MBhyRCh/yIo6ZMXAV2GqU52tLDya28vGfgfPYE=";
          };
        };
        codedb = pkgs.stdenv.mkDerivation {
          pname = "codedb";
          version = codedbVersion;
          src = pkgs.fetchurl {
            url = "https://github.com/justrach/codedb/releases/download/v${codedbVersion}/${codedbAsset.name}";
            inherit (codedbAsset) hash;
          };
          dontUnpack = true;
          dontConfigure = true;
          dontBuild = true;
          installPhase = ''
            runHook preInstall
            install -Dm755 "$src" "$out/bin/codedb"
            runHook postInstall
          '';
          meta = {
            description = "Local code intelligence for AI agents";
            homepage = "https://github.com/justrach/codedb";
            license = lib.licenses.bsd3;
            mainProgram = "codedb";
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
        pymupdfAsset = builtins.getAttr system {
          aarch64-darwin = {
            url = "https://files.pythonhosted.org/packages/fa/01/3591f781b417b382a8487a2356e927acfe858b1043bab0ec47f6805bb109/pymupdf-1.28.2-cp310-abi3-macosx_11_0_arm64.whl";
            hash = "sha256-cROEazXb8KAz8Ijk9PtUPavrSwsSwRKWahyh7i1erK4=";
          };
          aarch64-linux = {
            url = "https://files.pythonhosted.org/packages/d2/86/4a68f080b71b46802178346af46486e1697508e760855ff5f3b218a6dff7/pymupdf-1.28.2-cp310-abi3-manylinux_2_28_aarch64.whl";
            hash = "sha256-MFCiM93hIR7+ia2nTirdYjhDZDQVn0YJehQjqtKEJUU=";
          };
          x86_64-linux = {
            url = "https://files.pythonhosted.org/packages/c7/06/dace3e27af26690cb20bead80dbac42941b0841eb689b8aabbd67dde16f0/pymupdf-1.28.2-cp310-abi3-manylinux_2_28_x86_64.whl";
            hash = "sha256-OX1nFcHw33VIqS0K/YzjcPxI+keu76wWvivAShaoIn8=";
          };
        };
        pymupdf = pythonPackages.buildPythonPackage {
          pname = "pymupdf";
          version = "1.28.2";
          format = "wheel";
          src = pkgs.fetchurl pymupdfAsset;
          nativeBuildInputs = lib.optionals pkgs.stdenv.isLinux [pkgs.autoPatchelfHook];
          buildInputs = lib.optionals pkgs.stdenv.isLinux [
            pkgs.glibc
            pkgs.stdenv.cc.cc.lib
          ];
          doCheck = false;
          pythonImportsCheck = ["pymupdf"];
        };
        onnxruntimeAsset = builtins.getAttr system {
          aarch64-darwin = {
            url = "https://files.pythonhosted.org/packages/98/f8/dcbe7700dca82fa540035abd3c868fe5ad0f86af00b9a3db7c2e27d15c7d/onnxruntime-1.28.0-cp312-cp312-macosx_14_0_arm64.whl";
            hash = "sha256-Jv8P3QbvtsFVuulTh6CdsaK+icegPk0L/9WhccwoJto=";
          };
          aarch64-linux = {
            url = "https://files.pythonhosted.org/packages/28/5b/1d77e62097fdbe07e2dc827f389b1c4c0c275f6fab0369a8f46d2461af27/onnxruntime-1.28.0-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl";
            hash = "sha256-ToGiPfFuesudUbBtMMwJjkkxXvkYD5e8IiHRZ7SwTZw=";
          };
          x86_64-linux = {
            url = "https://files.pythonhosted.org/packages/95/df/5486ab03e9be288d5268867054c8b04bebcf95bfd12e801c05cc67703dab/onnxruntime-1.28.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl";
            hash = "sha256-CoO9tw0UPO3nYrZ3eJvyp6zKVLP7glZWAdXDBpWqkzw=";
          };
        };
        onnxruntime = pythonPackages.buildPythonPackage {
          pname = "onnxruntime";
          version = "1.28.0";
          format = "wheel";
          src = pkgs.fetchurl onnxruntimeAsset;
          dependencies = with pythonPackages; [
            flatbuffers
            numpy
            packaging
            protobuf
          ];
          nativeBuildInputs = lib.optionals pkgs.stdenv.isLinux [pkgs.autoPatchelfHook];
          buildInputs = lib.optionals pkgs.stdenv.isLinux [
            pkgs.glibc
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];
          doCheck = false;
          pythonImportsCheck = ["onnxruntime"];
        };
        pymupdfLayoutAsset = builtins.getAttr system {
          aarch64-darwin = {
            url = "https://files.pythonhosted.org/packages/16/1f/f03250cb18d4942d16f335d90a7eef2411b29097ba52531e0062edf16186/pymupdf_layout-1.28.2-cp310-abi3-macosx_11_0_arm64.whl";
            hash = "sha256-1V6bkVDh6fGCBjuTCS8LosJHW1HqbaZW6dbtrQ3UBU0=";
          };
          aarch64-linux = {
            url = "https://files.pythonhosted.org/packages/75/82/6cbf0331e148db48bf609c165dbe900cf3c1158546c5d09d4ad7fd4d6b17/pymupdf_layout-1.28.2-cp310-abi3-manylinux_2_28_aarch64.whl";
            hash = "sha256-/HcWaCv94mwAKnMJzaDFIPPSRm3ONoBU1PwrZTp06Lw=";
          };
          x86_64-linux = {
            url = "https://files.pythonhosted.org/packages/03/65/6b92d25678c64839fb2066ee98d6d1f164d820ba045d83c77e79021cda98/pymupdf_layout-1.28.2-cp310-abi3-manylinux_2_28_x86_64.whl";
            hash = "sha256-S0Sh2Ov4l7DoYu4tc+ffcwmfHAR/wCTS3STvBjLSy18=";
          };
        };
        pymupdf4llmAsset = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/7d/93/0ec4c33150f127d19b306d876b969755f02ed721f3a9337fd1f4fe4a1c85/pymupdf4llm-1.28.2-py3-none-any.whl";
          hash = "sha256-VcBsB9Eo+UxNknG9Qn0W7iGXeefXlqe52k4RC+MATZY=";
        };
        # These wheels extend the regular `pymupdf` package directory. Keeping
        # them in one output preserves Python's package lookup invariant.
        pymupdf4llm = pymupdf.overrideAttrs (old: {
          pname = "pymupdf-document-stack";
          nativeBuildInputs =
            (old.nativeBuildInputs or [])
            ++ [pkgs.unzip];
          dependencies = with pythonPackages; [
            networkx
            numpy
            onnxruntime
            psutil
            pyyaml
            tabulate
          ];
          propagatedBuildInputs =
            (with pythonPackages; [
              networkx
              numpy
              psutil
              pyyaml
              tabulate
            ])
            ++ [onnxruntime];
          postInstall = ''
            site="$out/${pythonPackages.python.sitePackages}"
            ${pkgs.unzip}/bin/unzip -qo ${pkgs.fetchurl pymupdfLayoutAsset} -d "$site"
            ${pkgs.unzip}/bin/unzip -qo ${pymupdf4llmAsset} -d "$site"
          '';
          pythonImportsCheck = [
            "pymupdf"
            "pymupdf.layout"
            "pymupdf4llm"
          ];
        });
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
          [lean4Pinned pkgs.git lightpanda codedb]
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
              pymupdf4llm
              treeSitter
              treeSitterLanguagePack
            ];

          nativeBuildInputs = [pkgs.makeWrapper];
          doCheck = false;
          pythonImportsCheck = [
            "autolean.agent"
            "autolean.finetune"
          ];
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
        sandboxPolicyTest =
          pkgs.runCommand "autolean-generated-code-sandbox-policy" {
            nativeBuildInputs = [sandboxTestPython];
          } ''
            cd ${source}
            PYTHONPATH=${source} \
              ${sandboxTestPython}/bin/python -m pytest -q \
                -p no:cacheprovider \
                tests/test_lean_interface.py \
                -k linux_sandbox
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
            codedb = codedb;
            default = autolean;
            lean = lean4Pinned;
            lean-grammar = leanGrammar;
            lightpanda = lightpanda;
          }
          // lib.optionalAttrs pkgs.stdenv.isLinux {
            generated-code-sandbox-vm = sandboxVmTest;
          };

        devShells = {
          ci = pkgs.mkShell {
            name = "autolean-ci";
            packages = with pkgs; [
              actionlint
              cffconvert
              lychee
            ];
          };

          default = pkgs.mkShell {
            name = "autolean";
            packages =
              (with pkgs; [
                actionlint
                cffconvert
                cvc5
                curl
                jq
                lychee
                ollama
                ripgrep
                tmux
                tesseract
                uv
                vhs
                z3
                zstd
              ])
              ++ runtimeTools
              ++ [autolean sandboxTestPython];

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
            generated-code-sandbox-policy = sandboxPolicyTest;
          };

        formatter = pkgs.alejandra;
      }
    );
}
