# Python installation and dependencies

The Nix development shell is the release-qualified environment. It includes
the Python application, Lean closure, native sandbox tools, Lightpanda, CodeDB,
and the PDF extraction stack.

The distribution name is `autolean-proof`. It installs the `autolean` Python
package and command. The distribution separates the MIT application core from
optional provider and document runtimes:

| Installation | Capability |
| --- | --- |
| `autolean-proof` | CLI, workbench, subscription CLIs, HTML papers, Lean structure |
| `autolean-proof[pdf]` | PyMuPDF4LLM and PyMuPDF Layout PDF extraction |
| `autolean-proof[anthropic]` | Anthropic API provider |
| `autolean-proof[openai]` | OpenAI API provider |
| `autolean-proof[lean]` | `lean-interact` integration |
| `autolean-proof[all]` | Every optional runtime above |

For a uv checkout, install the locked PDF stack with:

```bash
uv sync --extra pdf
```

AutoLean source is MIT licensed. Each dependency retains its own license.
PyMuPDF and PyMuPDF4LLM are available under GNU AGPL terms or a commercial
license from Artifex. Select the PDF extra only under terms suitable for the
application. The generated CycloneDX SBOM records the complete dependency
graph for each release.

See the [PyMuPDF license documentation] and
[PyMuPDF4LLM licensing FAQ] for the upstream terms.

[PyMuPDF license documentation]: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
[PyMuPDF4LLM licensing FAQ]: https://pymupdf.readthedocs.io/en/latest/faq/index.html
