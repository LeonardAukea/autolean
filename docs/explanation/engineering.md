# Engineering discipline

AutoLean is maintained as a living system. Engineering integrates code over
time and people: users depend on its behaviour, operators depend on its
failures being visible, and future maintainers depend on its structure being
clear.

A change is ready when its contract is explicit, its implementation is
readable, its failures are actionable, and its evidence covers the boundary it
claims.

## Start with the contract

State the behaviour before choosing the mechanism. A useful contract names:

- the user-visible result;
- valid inputs and owned state;
- the invariant that must continue to hold;
- the failure result and preserved state;
- the evidence that decides acceptance.

Correctness has meaning only against that contract. An ambiguous request is a
design task before it is a programming task.

## Make failure visible

Reliable software reports success only for a correct result and exposes every
failure. A boundary error states the action that failed, the relevant object,
whether state changed, and the next safe action when one exists.

AutoLean records exact source, model, environment, and validation identities
because a proof result without its conditions cannot be reproduced. Its
compare-and-swap edits and immutable releases preserve the state that was
actually checked.

## Write for the next reader

Code is read throughout its lifetime. Prefer direct data flow, small named
values, and one owner for each decision. An abstraction earns its name by
removing a concept from its callers. A comment carries a constraint or reason
that the code cannot express.

Readability enables correction, performance work, portability, and safe
change. Clever code consumes the understanding needed to debug it later.

## Debug from facts

A failure is evidence. Begin with the smallest reproduction and record:

- exact input and command;
- expected and observed behaviour;
- versioned environment;
- complete diagnostic output;
- relevant logs or generated artifacts.

Form theories after preserving those facts. Add a regression test that exposes
the failure, then repair the smallest owner that violates the contract. The
test remains after the repair and preserves the contract across later changes.

## Test behaviour over time

Tests preserve behaviour through refactors, dependency changes, new
interpreters, and new machines. A useful test is readable and explains the
contract through its inputs and assertions. It covers success, rejection, and
the edge that separates them.

Functional evidence exercises the installed interface. Integration evidence
exercises boundaries between owners. End-to-end evidence establishes only the
environment and path it actually ran. Every report keeps that qualification
boundary explicit.

## Work as one continuing team

Small, scoped changes let reviewers understand cause and effect. Compatibility
keeps existing users working while the system evolves. Documentation gives
each fact one home and links to it from the places where readers need it.

Review code, tests, prose, and operational output with respect for their next
reader. Record the invariant and evidence clearly enough that another
maintainer can continue the work from the current contract.

## Change loop

Each change follows the same loop:

1. specify the contract and qualification boundary;
2. reproduce the current behaviour with exact evidence;
3. implement the smallest coherent owner;
4. run focused tests, then the repository gates;
5. review readability, diagnostics, compatibility, and documentation;
6. publish only the exact qualified revision and retain its evidence.

The loop ends with a result another person can inspect, repeat, and maintain.
