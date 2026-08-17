Output docs/assets/autolean-ionescu-tulcea.gif
Output docs/assets/autolean-ionescu-tulcea.mp4

Require autolean

Set Shell "bash"
Set FontSize 16
Set Width 1280
Set Height 1200
Set Framerate 24
Set PlaybackSpeed 3.0
Set TypingSpeed 20ms
Set Theme "Catppuccin Mocha"
Set Padding 18
Set Margin 24
Set MarginFill "#181825"
Set WindowBar Colorful
Set BorderRadius 10
Set CursorBlink false

Hide
Type `cd "$AUTOLEAN_DEMO_ROOT"`
Enter
Type `export PS1='autolean ❯ '`
Enter
Type "clear"
Enter
Show

Type "autolean --help"
Enter
Sleep 7s
Type "clear"
Enter
Sleep 1s
Hide
Type `autolean() { command autolean "$@"; status=$?; if test "$status" -eq 0 && test "${1:-}" = verify; then printf '\nDEMO_VERIFY_COMPLETE'; read -r _; printf '\033[1A\r\033[2K'; fi; return "$status"; }`
Enter
Type "clear"
Enter
Show

Type `autolean verify "$AUTOLEAN_DEMO_PDF" --review-plan --max-cycles 5`
Enter
Wait@600s /Use this paper plan?/
Sleep 4s
Type "n"
Enter
Wait@30s /Additional guidance/
Type "Separate the executable alias-elaboration audit from follow-up work. Do not claim signature comparison, mapping grades, paper-form equivalence, or per-declaration axiom audits."
Enter
Wait@600s /Use this paper plan?/
Sleep 4s
Type "y"
Enter
Wait@600s /DEMO_VERIFY_COMPLETE/
Enter
Sleep 6s

Type "clear"
Enter
Sleep 1s
Type `autolean sessions`
Enter
Sleep 5s

Hide
Type `session="$(autolean sessions --json | jq -r '.[0].id')"`
Enter
Show
Type `autolean export paper-artifact --session "$session" --title "Ionescu-Tulcea paper audit"`
Enter
Sleep 8s

Type "clear"
Enter
Sleep 1s
Hide
Type `plan="$(find workspace/AutoLean/Papers -name '*_plan_*.json' -print -quit)"`
Enter
Show
Type `jq '{schema, accepted_response_model, attempts: [.responses[] | {attempt, model, validation_error, response_sha256}]}' "$plan"`
Enter
Sleep 8s

Type "clear"
Enter
Sleep 1s
Hide
Type `coverage="$(find workspace/AutoLean/Papers -name '*_coverage_*.json' -print -quit)"`
Enter
Show
Type `jq '{schema, total_items, elaborated_items, profile: .profile.id, lean_evidence}' "$coverage"`
Enter
Sleep 8s

Type "clear"
Enter
Sleep 1s
Type `find paper-artifact -maxdepth 3 -type f | sort`
Enter
Sleep 7s

Type `cd workspace && lake env lean AutoLean/Paper_arxiv_2506_18616v5.lean && echo "Lean accepted every mapped declaration"`
Enter
Wait@300s /Lean accepted every mapped declaration/
Sleep 5s
