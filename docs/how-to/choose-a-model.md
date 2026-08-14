# Choose and switch models

AutoLean gives every model backend the same narrow job: return text for a
bounded request. Lean validation is identical across providers.

## Inspect local readiness

```bash
autolean models
```

The command lists profiles, their backends, capabilities, setup commands, and
the readiness AutoLean can observe. Use a profile name with any model-aware
workflow:

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
configured hosted API. Claude is the stable tie-breaker when both Claude and
Codex subscriptions are ready. The selected provider always receives its
strongest tuned profile and `max` reasoning effort:

- Claude CLI: `fable`
- Codex CLI: `gpt-5.6-sol`
- Anthropic API: `claude-fable-5`
- OpenAI API: `gpt-5.6-sol`

The mappings follow Anthropic's
[model guidance](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model)
and [effort control](https://platform.claude.com/docs/en/build-with-claude/effort),
and OpenAI's [model catalog](https://developers.openai.com/api/docs/models).

Choose a provider while retaining its strongest model with `--backend`:

```bash
autolean doctor --backend codex_cli
autolean prove "1 + 1 = 2" --backend anthropic
```

Select the exact model for local and self-hosted backends.

## Set the project default

Put the profile name in `program.md` when a project should begin every run with
the same model:

```markdown
## LLM Configuration

model: sonnet
```

A command-line `--model` selects a model for that invocation. The session
records the resolved profile, backend, and provider model.

## Choose interactively

```bash
autolean workbench
```

The workbench lists ready models and lets you select one before starting or
continuing a proof session.

## Use a subscription

`claude_cli` uses a Claude subscription authenticated by the Claude CLI.
`codex_cli` uses a ChatGPT subscription authenticated by the Codex CLI.

```bash
claude                    # enter /login
codex login
autolean doctor --model opus
autolean doctor --model codex
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
backend: openai_compat
endpoint: http://127.0.0.1:8080
temperature: 0
```

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
