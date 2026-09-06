Output docs/assets/autolean-ionescu-tulcea.gif
Output docs/assets/autolean-ionescu-tulcea.mp4

Require autolean
Require bash
Require clear
Require codex
Require find
Require jq
Require lake
Require python3
Require sort

Source docs/demos/style.tape

Hide
Type `cd "$AUTOLEAN_DEMO_ROOT"`
Enter
Type `export PS1='autolean ❯ '`
Enter
Type `autolean() { command autolean "$@"; status=$?; if test "$status" -eq 0 && test "${1:-}" = verify; then printf '\nDEMO_VERIFY_COMPLETE'; read -r _; printf '\033[1A\r\033[2K'; fi; return "$status"; }`
Enter
Type `paper_lean_check() { source="$(find AutoLean -maxdepth 1 -type f -name 'Paper_*.lean' -print -quit)"; python3 "$AUTOLEAN_DEMO_WORKSPACE_HELPER" "$AUTOLEAN_DEMO_ROOT/workspace/.lake" .lake && lake env lean "$source"; status=$?; if test "$status" -eq 0; then printf '\nLean accepted every exported declaration'; else printf '\nLean check failed'; fi; printf '\nDEMO_PAPER_LEAN_FINISHED'; read -r _; printf '\033[1A\r\033[2K'; return "$status"; }`
Enter
Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show

Type `autolean verify "$AUTOLEAN_DEMO_PDF" --model codex --review-plan --max-cycles 5`
Hide
Enter
Wait@900s /Use this paper plan?/
Show
Sleep 6s
Type "n"
Enter
Wait@30s /Additional guidance/
Type "Separate the executable alias-elaboration audit from follow-up work. Do not claim signature comparison, mapping grades, paper-form equivalence, or per-declaration axiom audits."
Hide
Enter
Wait@900s /Use this paper plan?/
Show
Sleep 6s
Type "y"
Hide
Enter
Wait@900s /DEMO_VERIFY_COMPLETE/
Enter
Show
Sleep 6s

Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 500ms
Type `autolean sessions`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 5s

Hide
Type `session="$(autolean sessions --json | jq -r '.[0].id')"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Type `autolean export paper-artifact --session "$session" --title "Ionescu-Tulcea paper audit"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 6s

Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 500ms
Hide
Type `plan="$(find workspace/AutoLean/Papers -name '*_plan_*.json' -print -quit)"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Type `jq '{schema, accepted_response_model, responses: [.responses[] | {model, guidance_count: (.guidance | length), validation_error, response_sha256}]}' "$plan"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 6s

Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 500ms
Hide
Type `coverage="$(find workspace/AutoLean/Papers -name '*_coverage_*.json' -print -quit)"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Type `jq '{schema, total_items, elaborated_items, profile: .profile.id, lean_evidence}' "$coverage"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 6s

Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 500ms
Type `find paper-artifact -maxdepth 3 -type f | sort`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 6s

Type `cd paper-artifact/project && paper_lean_check`
Hide
Enter
Wait@300s /DEMO_PAPER_LEAN_FINISHED/
Enter
Show
Sleep 5s
