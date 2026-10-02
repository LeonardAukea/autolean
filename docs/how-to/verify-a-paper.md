# Verify a paper

`autolean verify` acquires a paper, extracts its mathematical items, and
prepares Lean source. For ordinary papers it asks a model to formalize proof
obligations. For an exact paper revision with a reviewed profile, it checks
the profile's mappings to existing Lean declarations. The
[coverage reference](../reference/research-artifacts.md#paper-coverage)
defines what each result establishes.

## Extract the source

```bash
autolean verify https://arxiv.org/abs/2404.12534 --extract-only
```

AutoLean tries native arXiv HTML first, then the pinned Lightpanda renderer,
then the paper PDF, and finally the arXiv abstract. HTML extraction preserves
theorem and proof environments and MathML alternative text. The PDF path uses
PyMuPDF4LLM and PyMuPDF Layout for reading order, tables, formulas, and
selective OCR. A source that yields no text leaves a title-only document,
which extracts no claims.

The Nix shell includes the PDF runtime. A uv checkout installs it explicitly:

```bash
uv sync --extra pdf
```

Restrict a large PDF to relevant pages:

```bash
autolean verify paper.pdf --extract-only --pages 12-19
```

Extracted Markdown is written under `AutoLean/Papers`. The exact acquired PDF
is stored by content hash under `.autolean/papers`.

## Use a document service for difficult scans

PaddleOCR-VL handles pages that mix scans, formulas, tables, charts, and layout.
Run the service on infrastructure you control, then pass its explicit endpoint:

```bash
autolean verify scans.pdf \
  --extract-only \
  --pdf-engine paddleocr-vl \
  --paddleocr-url http://127.0.0.1:8080
```

The service receives the selected PDF pages. AutoLean records its endpoint,
local or remote placement, exact transferred byte count and hash, page set,
and output hash in the paper coverage ledger.

## Review the formalization

Generate Lean without starting proof search:

```bash
autolean verify paper.pdf --formalize-only
```

Compare every generated declaration with the source. Check definitions,
quantifiers, hypotheses, coercions, conventions, and the claimed conclusion.
Edit the Lean statement until it expresses the source faithfully.

For example, omitting a hypothesis or reversing a quantifier can change the
claim. A valid proof settles the resulting Lean statement; correspondence to
the author's claim remains a mathematical review obligation.

## Attempt the reviewed claims

```bash
autolean solve --target DECLARATION_NAME --max-cycles 5
```

Use the declaration name from the Lean source you reviewed. To run acquisition,
formalization, and proof search together, use
`autolean verify paper.pdf --max-cycles 5`. Use `--output` during verification
to choose the Lean module. Model selection uses the shared `--model` and
`--provider` options.

Hosted Anthropic and OpenAI providers receive a native PDF containing only the
selected pages and bounded, page-addressed Markdown. The coverage ledger
records the effective inference placement, request and response hashes, PDF
hash and byte count, and transferred page numbers. Other providers receive
bounded Markdown. Read
[Trust boundary](../explanation/trust-boundary.md) before sending unpublished
or confidential papers to a provider.
