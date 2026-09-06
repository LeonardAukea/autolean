# Python installation and dependencies

The Nix development shell is the release-qualified environment. It includes
the Python application, Lean closure, native sandbox tools, Lightpanda, CodeDB,
and the PDF extraction stack.

The distribution name is `autolean-proof`. It installs the `autolean` Python
package and command. The distribution separates the MIT application core from
optional provider and document runtimes:

| Installation | Capability |
| --- | --- |
| `autolean-proof` | CLI, workbench, Claude/Codex/Grok CLIs, HTML papers |
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

Install the application from this checkout or a downloaded release wheel:

```bash
uv tool install .
# Or, after downloading a release wheel:
uv tool install ./autolean_proof-0.5.0-py3-none-any.whl
```

That command needs a Lean toolchain to check anything, which
[elan](https://github.com/leanprover/elan) supplies, and a Lean project,
which `autolean init lean` creates:

```bash
autolean init lean
cd lean && lake update && lake exe cache get && lake build
```

Linux also requires Bubblewrap (`bwrap`) with user namespaces enabled. macOS
uses its supplied `sandbox-exec`. The Nix shell supplies the pinned Lean,
Mathlib, CSLib, and sandbox tools, with archive hashes checked during setup.
Every proof environment records the actual installed toolchain and artifacts.
The [release guide](../how-to/release.md#6-publish-the-python-distribution)
owns registry publication and its trusted-publisher requirements.

A checkout also carries a Homebrew formula. It installs one pinned immutable
release with every runtime dependency as a locked resource and depends on
elan for the Lean toolchain:

```bash
brew install --build-from-source Formula/autolean.rb
```

The [release guide](../how-to/release.md) moves the formula to a new release.

AutoLean source is MIT licensed. Each dependency retains its own license.
PyMuPDF and PyMuPDF4LLM are available under GNU AGPL terms or a commercial
license from Artifex. Select the PDF extra only under terms suitable for the
application. The generated CycloneDX SBOM records the complete dependency
graph for each release.

See the [PyMuPDF license documentation] and
[PyMuPDF4LLM licensing FAQ] for the upstream terms.

[PyMuPDF license documentation]: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
[PyMuPDF4LLM licensing FAQ]: https://pymupdf.readthedocs.io/en/latest/faq/index.html
