Output docs/assets/autolean-pythagorean.gif
Output docs/assets/autolean-pythagorean.mp4

Require autolean
Require bash
Require codex
Require clear
Require find
Require git
Require jq
Require lake
Require python3
Require sort

Source docs/demos/style.tape

Hide
Type `cd "$AUTOLEAN_DEMO_ROOT"`
Enter
Type `export PS1='autolean ❯ ' GIT_PAGER=cat`
Enter
Type `guide="State it geometrically: for points p1 p2 p3 in a Euclidean affine space with angle p1 p2 p3 = pi / 2, dist p1 p3 ^ 2 = dist p1 p2 ^ 2 + dist p2 p3 ^ 2. Close with Mathlib's EuclideanGeometry.dist_sq_eq_dist_sq_add_dist_sq_iff_angle_eq_pi_div_two."`
Enter
Type `autolean() { command autolean "$@"; status=$?; if test "${1:-}" = prove; then printf '\nDEMO_PROVE_FINISHED'; read -r _; if test "$status" -ne 0; then exit "$status"; fi; printf '\033[1A\r\033[2K'; fi; return "$status"; }`
Enter
Type `demo_lean_check() { python3 "$AUTOLEAN_DEMO_WORKSPACE_HELPER" "$AUTOLEAN_DEMO_ROOT/workspace/.lake" .lake && lake env lean "$1"; status=$?; if test "$status" -eq 0; then printf '\nLean accepted the proof'; else printf '\nLean check failed'; fi; printf '\nDEMO_LEAN_FINISHED'; read -r _; printf '\033[1A\r\033[2K'; return "$status"; }`
Enter
Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show

Type `autolean prove "the Pythagorean theorem" --model codex --review-plan --guide "$guide" --max-attempts 5`
Hide
Enter
Wait@900s /Use this plan?/
Show
Sleep 6s
Type "y"
Hide
Enter
Wait@1200s /DEMO_PROVE_FINISHED/
Enter
Show
Sleep 5s

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

Type `git -C workspace log -1 --stat`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 5s

Type "clear"
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 500ms
Hide
Type `session="$(autolean sessions --json | jq -r '.[0].id')"`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Type `autolean export pythagorean-artifact --session "$session" --title "A checked Pythagorean theorem"`
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
Type `find pythagorean-artifact -maxdepth 4 -type f | sort`
Hide
Enter
Wait@120s /^autolean ❯ *$/
Show
Sleep 6s

Type `cd pythagorean-artifact/project && generated="$(find AutoLean/Generated -maxdepth 1 -type f -name '*.lean' -print -quit)" && demo_lean_check "$generated"`
Hide
Enter
Wait@300s /DEMO_LEAN_FINISHED/
Enter
Show
Sleep 5s
