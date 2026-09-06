# Choose and switch models

AutoLean gives every model provider the same narrow job: return text for a
bounded request. Lean validation is identical across providers.

## Inspect local readiness

```bash
autolean models
autolean models codex
autolean models codex-terra
autolean models codex --json
```

The catalog lists every profile and the readiness AutoLean can observe. Pass a
provider name to compare only its models, or a profile name to see its exact
provider model, inference placement, setup, and selection command. `--json`
emits the same selected catalog with provider capabilities for scripts. Use a
profile with any model-aware workflow:

```bash
autolean plan "every prime greater than two is odd" --model opus
autolean prove "1 + 1 = 2" --model codex-luna
autolean solve --model deepseek-prover
```

The registry in `autolean/models.py` is the source of truth for shipped
profiles. Provider catalogs change; the command reports the profiles in the
installed AutoLean version.

## Use the machine default

`model: auto` selects an authenticated subscription CLI first, followed by a
configured hosted API. Subscription priority is Codex, Claude, then Grok;
hosted API priority is OpenAI, then Anthropic. Codex selects GPT-6 Astra at
`max` reasoning effort. Each provider selects its tuned default profile:

- Claude CLI: `fable` at `max`
- Codex CLI: `gpt-6-astra` at `max`
- Grok CLI: `grok-4.6` at `xhigh`
- Anthropic API: `claude-fable-5` at `max`
- OpenAI API: `gpt-6-astra` at `max`

The provider names are `claude`, `codex`, `grok`, `anthropic`, `openai`,
`ollama`, `compatible`, and `muse`. The mappings follow Anthropic's
[model guidance](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model)
and [effort control](https://platform.claude.com/docs/en/build-with-claude/effort),
and OpenAI's
[GPT-6 Astra contract](https://developers.openai.com/api/docs/models/gpt-6-astra).
`grok models` lists the Grok CLI's live catalog.

Choose a provider while retaining its strongest model with `--provider`:

```bash
autolean doctor --provider grok
autolean prove "1 + 1 = 2" --provider anthropic
autolean --provider grok prove "the Pythagorean theorem"
```

Local and self-hosted providers require an exact model profile or model ID.
The [CLI reference](../reference/cli.md#shared-model-options) records the
provider names used by configuration and automation.

## Set the project default

Put the profile name in `program.md` when a project should begin every run with
the same model:

```markdown
## LLM Configuration

model: sonnet
```

A command-line `--model` selects a model for that invocation. The session
records the resolved provider and provider model.

## Choose interactively

```bash
autolean workbench
```

The workbench lists ready models and lets you select one before starting or
continuing a proof session.

## Use a subscription

The `claude` provider uses a Claude subscription authenticated by the Claude
CLI. The `codex` provider uses a ChatGPT subscription authenticated by the
Codex CLI. The `grok` provider uses a SuperGrok or X Premium+ subscription
authenticated by the Grok CLI.

```bash
claude                    # enter /login
codex login
grok login
autolean doctor --model opus
autolean doctor --model codex
autolean doctor --model grok
```

Subscription subprocesses run in temporary directories with provider tools,
project rules, and session persistence disabled. Provider API keys are removed
from their environments so an API login cannot satisfy a subscription profile.

## Use a hosted API

Set the provider credential in the process environment and select an API
profile:

```bash
export ANTHROPIC_API_KEY=...
autolean prove "1 + 1 = 2" --model opus-api

export OPENAI_API_KEY=...
autolean prove "1 + 1 = 2" --model gpt-api
```

The [provider boundary](../explanation/trust-boundary.md#provider-boundary)
states how credentials and prompt data are handled.

## Use local inference

Ollama profiles need only the named local model:

```bash
ollama pull yinyaowenhua1314/deepseek-prover-v2-7b
autolean doctor --model deepseek-prover
```

For llama.cpp, vLLM, or another OpenAI-compatible server, put the endpoint in
`program.md` and select the wire protocol explicitly:

```markdown
## LLM Configuration

model: my-local-model
provider: compatible
endpoint: http://127.0.0.1:8080
search_scope: local
temperature: 0
```

`localhost` and loopback IP endpoints are local. Any other explicit endpoint
is remote. With `search_scope: auto`, local inference also keeps theorem and
goal searches local; `search_scope: remote` composes local inference with
Loogle, LeanSearch, and arXiv.

The `muse-glimmer` profiles add the reasoning controls and stop-token semantics
required by Muse Glimmer. `autolean models` prints the qualified model revision,
weight identity, and server setup owned by the installed profile.

## Switch during a session

Sessions retain the target, plan, failure evidence, and accepted artifacts.
Change only the model when you continue:

```bash
autolean sessions --active
autolean resume SESSION_ID --model opus
```

Add new mathematical information with `--guide`:

```bash
autolean resume SESSION_ID \
  --model opus \
  --guide "Reduce first to the finite-dimensional subspace spanned by x and y."
```

## Escalate on proof evidence

The `ask` policy offers one stronger model after repeated kernel-facing proof
failures. `auto` permits that switch without a prompt. `never` keeps the model
fixed.

```bash
autolean solve \
  --model codex-luna \
  --escalation ask \
  --escalate-after 2 \
  --escalate-to codex
```

Authentication, quota, network, project, and Lake failures stop at their own
boundary. They are not evidence that a larger model will solve the theorem.

See [`program.md` configuration](../reference/program.md) for persistent
settings and [Trust boundary](../explanation/trust-boundary.md) before sending
private source to a hosted provider.
