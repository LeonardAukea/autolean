import Lake
open Lake DSL

package autolean_workspace where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "v4.34.1"

require cslib from git
  "https://github.com/leanprover/cslib" @ "v4.34.1"

@[default_target]
lean_lib AutoLean where
  srcDir := "."
