import Lake
open Lake DSL

package autolean_workspace where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]
  moreLinkArgs := #["-L./.lake/packages/mathlib/.lake/build/lib", "-lMathlib"]

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "v4.29.0"

@[default_target]
lean_lib AutoLean where
  srcDir := "."
