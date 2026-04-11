import Lake
open Lake DSL

package autolean_workspace where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]

@[default_target]
lean_lib AutoLean where
  srcDir := "."
