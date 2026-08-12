# Prove a first theorem

This tutorial starts from a clean checkout and ends with a standalone Lean
project containing a kernel-checked proof.

## 1. Enter the pinned environment

```bash
git clone https://github.com/LeonardAukea/autolean.git
cd autolean
nix develop
```

The shell supplies `autolean`, Lean, Lake, Mathlib, CSLib, and the native
sandbox tools. Check the available model profiles:

```bash
autolean models
```

The automatic default uses an authenticated Claude or Codex subscription at
maximum reasoning effort. Sign in to either CLI, then leave that session. See
[Choose and switch models](../how-to/choose-a-model.md) for selection rules,
API profiles, and local models.

## 2. Create a project

Keep the tutorial separate from the AutoLean source checkout:

```bash
cd ..
mkdir autolean-first-proof
cd autolean-first-proof
autolean init lean
```

This creates `program.md` beside a Lean project in `lean/`. Fetch the pinned
library revisions and their compiled cache:

```bash
cd lean
lake update
lake exe cache get
lake build
cd ..
```

## 3. Check the complete path

```bash
autolean doctor
```

`doctor` checks model authentication, asks the model for a small proof,
validates that proof in the operating-system sandbox, audits its declaration
and axioms, and builds the trusted project.

Stop here if any check fails. `autolean models` reports model setup; Lean and
sandbox failures include the failing command boundary.

## 4. Review a plan

```bash
autolean prove "the Pythagorean theorem" --review-plan
```

AutoLean first presents a mathematical plan. Read the formalization target,
premises, reductions, completion criterion, and checkpoints. Accept the plan
or revise it before Lean source is generated.

The formalization phase compiles the proposed declaration in isolation. Proof
search begins only after the statement compiles. A successful proof then
passes the same sandbox, declaration-range, environment, and axiom checks used
by `doctor`.

## 5. Inspect the result

List the durable session and the proof commit:

```bash
autolean sessions
git -C lean log -1 --stat
```

The accepted source is under `lean/AutoLean/Generated/`. It contains the exact
declaration and proof checked by Lean. The session record binds that proof to
its model, prompt, environment, and axiom report.

## 6. Export the artifact

```bash
autolean export pythagorean-artifact \
  --title "A checked Pythagorean theorem"
```

The export contains a standalone Lean project, a provenance manifest, and a
companion LaTeX paper. Build the exported project before sharing it:

```bash
cd pythagorean-artifact/project
lake update
lake exe cache get
lake build
```

You now have a proof artifact independent of AutoLean's runtime state. Continue
with [Run a research session](../how-to/run-a-research-session.md) when the
work requires several targets or model changes.
