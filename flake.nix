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
        leanVersion = "4.34.1";
        leanAsset = builtins.getAttr system {
          aarch64-darwin = {
            platform = "darwin_aarch64";
            hash = "sha256-ZfIqTwR3OOx0JmezJH6Daobr8G6rwSZVw97gBjfjeGY=";
          };
          aarch64-linux = {
            platform = "linux_aarch64";
            hash = "sha256-/bl0ws20Yn4JDV1AB7kT4J0TxIaHIPtVlOIoCLPenjc=";
          };
          x86_64-linux = {
            platform = "linux";
            hash = "sha256-R79LvXj3DC6WcFmKtxJNkrbvtzMP8z5fu0Aw9v1y5OQ=";
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
        lightpandaVersion = "0.4.1";
        lightpandaAsset = builtins.getAttr system {
          aarch64-darwin = {
            name = "lightpanda-aarch64-macos";
            hash = "sha256-meZ3Oe2M9bmFr3y/p8drK6slexcbLa0hEJvXS087tRA=";
          };
          aarch64-linux = {
            name = "lightpanda-aarch64-linux";
            hash = "sha256-Zkd1x/WracwxiZVMf5NF4lwWfLTazgFhc+Yp+aXoLEI=";
          };
          x86_64-linux = {
            name = "lightpanda-x86_64-linux";
            hash = "sha256-HUCAHnLAvGGyy9PzVivPxG3nt54FaPM/aGtk8uWHYQo=";
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
        codedbVersion = "0.2.5860";
        codedbAsset = builtins.getAttr system {
          aarch64-darwin = {
            name = "codedb-darwin-arm64";
            hash = "sha256-/YVooL2ctzWDzyC28KgXzvRHlbJQqYORQZ73yASfdRg=";
          };
          aarch64-linux = {
            name = "codedb-linux-arm64";
            hash = "sha256-6UrFJ01VRWUuy4iZ7yeXp8Kibe1jbKOnMCA0JqsZTG8=";
          };
          x86_64-linux = {
            name = "codedb-linux-x86_64";
            hash = "sha256-r0TV8CpsmiiQNomT1H/3p/pyUN3SJ4Xk6csvvKo6fQ4=";
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
          version = "8.5.0";
          src = pkgs.fetchPypi {
            pname = "click";
            version = "8.5.0";
            hash = "sha256-ug0gid516gMQ4t3gMWDmyhAAmUf7laGC+bVAIbsnLjQ=";
          };
        };
        hatchling = pythonPackages.hatchling.overridePythonAttrs (old: {
          version = "1.32.4";
          src = pkgs.fetchPypi {
            pname = "hatchling";
            version = "1.32.4";
            hash = "sha256-xEaPcxRMBU0qq07w8DeMQ7mHi/B/j/1reWkOlw03Xwc=";
          };
          dependencies = old.dependencies ++ [pythonPackages.tomlkit];
        });
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
            path = "fe/a7/7efe38f71d6487a533c45090e8df684d98f9ce817f86506bdc6a412fcafe/tree_sitter_language_pack-1.20.0-cp310-abi3-macosx_11_0_arm64.whl";
            hash = "sha256-TwsBVQRtkCfCyoai5nsPdAQL11IOqqZAuUvsuN8EL3I=";
          };
          aarch64-linux = {
            path = "e2/91/b89308f744e35d414a02efe2d122d051b74e275edb852fdcdd58238f1ab3/tree_sitter_language_pack-1.20.0-cp310-abi3-manylinux_2_34_aarch64.whl";
            hash = "sha256-NVw1BCmJ7Bdtyd7XCC0zV7ZoQp6oc/928rmmp9dTcxs=";
          };
          x86_64-linux = {
            path = "89/72/02da1179165c74930c489565d00388201c9d26533fdd3b61a5eae4cc347e/tree_sitter_language_pack-1.20.0-cp310-abi3-manylinux_2_34_x86_64.whl";
            hash = "sha256-5T+p4bKBwh8diFrmowgowq8rDEPyIbe8PDgfvexLFAk=";
          };
        };
        treeSitterLanguagePack = pythonPackages.buildPythonPackage {
          pname = "tree-sitter-language-pack";
          version = "1.20.0";
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
            url = "https://files.pythonhosted.org/packages/31/6f/48169f2e62b405bff5053cbd1d73fb5ce41ef7ecd13bb3bfcc191e689b8a/onnxruntime-1.30.0-cp312-cp312-macosx_14_0_arm64.whl";
            hash = "sha256-AB7XJsm9XivJL6refTfYielgajULfVUp8CJ98uO7V/0=";
          };
          aarch64-linux = {
            url = "https://files.pythonhosted.org/packages/16/bd/cbc5b8f91963689fdd622f463508c01d0aa95d3f944747b1e0b1eb2160b8/onnxruntime-1.30.0-cp312-cp312-manylinux_2_28_aarch64.whl";
            hash = "sha256-bDKgANUTmji6k0kDCwAy4zMay1WdWWsic42dKzQ6K4M=";
          };
          x86_64-linux = {
            url = "https://files.pythonhosted.org/packages/34/35/e7f862dbacbc99fadd9b14a614e49c99bf0f35fd9927a82f096e3de33531/onnxruntime-1.30.0-cp312-cp312-manylinux_2_28_x86_64.whl";
            hash = "sha256-+miOeJGmqiBmNv5zcuJ+51/RdxMon2tPx7GQ4Kfekyg=";
          };
        };
        onnxruntime = pythonPackages.buildPythonPackage {
          pname = "onnxruntime";
          version = "1.30.0";
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
            hash = "sha256-NPktnPTzrULZUUsw/rovyu3coNSvBkVDgP2eBjQnoJw=";
          };
          aarch64-linux = {
            platform = "linux-aarch64";
            extension = "so";
            hash = "sha256-Fq47yc4FlHCzPAv7foUXZ4VW8NXtpS/npjDBzuaAdTE=";
          };
          x86_64-linux = {
            platform = "linux-x86_64";
            extension = "so";
            hash = "sha256-9ypswG79xweF69KD7PriPmUPEiEYhzSYmFlMeHV9D1s=";
          };
        };
        grammarBundle = pkgs.fetchurl {
          url = "https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v1.20.0/parsers-${grammarBundleAsset.platform}.tar.zst";
          inherit (grammarBundleAsset) hash;
        };
        leanGrammar =
          pkgs.runCommand "tree-sitter-lean-1.20.0" {
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
          version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
          pyproject = true;
          src = source;

          build-system = [hatchling];
          dependencies =
            (with pythonPackages; [
              beautifulsoup4
              httpx
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
            "autolean.paper"
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
                curl
                ffmpeg-headless
                jq
                lychee
                ollama
                # PyMuPDF's selective OCR resolves its tessdata through the
                # tesseract binary on PATH.
                tesseract
                uv
                vhs
                zstd
              ])
              ++ runtimeTools
              ++ [autolean sandboxTestPython];

            shellHook = ''
              # The live checkout owns Python imports inside the development
              # shell while the packaged CLI supplies its executable.
              export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"

              first_line() {
                "$@" 2>/dev/null | head -n 1
              }
              echo "AutoLean dev shell"
              printf '  %-9s %s\n' autolean "$(first_line autolean --version)"
              printf '  %-9s %s\n' lean "$(first_line lean --version)"
              printf '  %-9s %s\n' uv "$(first_line uv --version)"
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
