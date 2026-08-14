# Python installation and dependencies

The Nix development shell is the release-qualified environment. It includes
the Python application, Lean closure, native sandbox tools, Lightpanda, CodeDB,
and the PDF extraction stack.

The distribution name is `autolean-proof`. It installs the `autolean` Python
package and command. The distribution separates the MIT application core from
optional provider and document runtimes:

| Installation | Capability |
| --- | --- |
| `autolean-proof` | CLI, workbench, subscription CLIs, HTML papers |
| `autolean-proof[structure]` | Tree-sitter structural context for prompts |
| `autolean-proof[pdf]` | PyMuPDF4LLM and PyMuPDF Layout PDF extraction |
| `autolean-proof[anthropic]` | Anthropic API provider |
| `autolean-proof[openai]` | OpenAI API provider |
| `autolean-proof[all]` | Every optional runtime above |

For a uv checkout, install the locked PDF stack with:

```bash
uv sync --extra pdf
```

## Installing without Nix

The distribution is a Python application. Installed on its own it supplies
the `autolean` command and nothing else:

```bash
uv tool install autolean-proof     # or: pipx install autolean-proof
```

That command needs a Lean toolchain to check anything, which
[elan](https://github.com/leanprover/elan) supplies, and a Lean project,
which `autolean init lean` creates:

```bash
autolean init lean
cd lean && lake update && lake exe cache get && lake build
```

The Nix shell remains the release-qualified environment: it pins the exact
Lean, Mathlib, and CSLib closure that every recorded proof identity names,
along with the sandbox tools. An elan-managed toolchain is whatever the
project's `lean-toolchain` resolves to on the day it is fetched, so two
machines can disagree. Prefer the Nix shell when a result has to be
reproducible; use the standalone install to try the command.

There is no Homebrew formula. A formula would have to pin every Python
dependency as its own resource and would still leave the Lean closure to
elan, so it would carry the maintenance of the Nix flake without its
guarantee.

AutoLean source is MIT licensed. Each dependency retains its own license.
PyMuPDF and PyMuPDF4LLM are available under GNU AGPL terms or a commercial
license from Artifex. Select the PDF extra only under terms suitable for the
application. The generated CycloneDX SBOM records the complete dependency
graph for each release.

See the [PyMuPDF license documentation] and
[PyMuPDF4LLM licensing FAQ] for the upstream terms.

[PyMuPDF license documentation]: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
[PyMuPDF4LLM licensing FAQ]: https://pymupdf.readthedocs.io/en/latest/faq/index.html
