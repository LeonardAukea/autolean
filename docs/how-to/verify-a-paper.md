# Verify a paper

`autolean verify` separates acquisition, formalization, and proof. Stop after
each boundary when a human needs to review the result.

## Extract the source

```bash
autolean verify https://arxiv.org/abs/2404.12534 --extract-only
```

AutoLean tries native arXiv HTML first, then the pinned Lightpanda renderer,
then the paper PDF. HTML extraction preserves theorem and proof environments
and MathML alternative text. The PDF path uses PyMuPDF4LLM and PyMuPDF Layout
for reading order, tables, formulas, and selective OCR.

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

The service receives the selected PDF pages. AutoLean records the extractor
input and output hashes with the acquired source.

## Review the formalization

Generate Lean without starting proof search:

```bash
autolean verify paper.pdf --formalize-only
```

Compare every generated declaration with the source. Check definitions,
quantifiers, hypotheses, coercions, conventions, and the claimed conclusion.
Edit the Lean statement until it is source-faithful.

Lean can prove a false rendition of the author's claim. Kernel acceptance
settles the formal statement only; source fidelity is a separate review
obligation.

## Attempt the reviewed claims

```bash
autolean verify paper.pdf --max-cycles 5
```

Use `--output` to choose the Lean module. Use `--model` and `--backend` exactly
as with `prove` and `solve`.

Hosted Anthropic and OpenAI backends receive the native PDF and page-addressed
Markdown. Other backends receive bounded Markdown. Read
[Trust boundary](../explanation/trust-boundary.md) before sending unpublished
or confidential papers to a provider.
