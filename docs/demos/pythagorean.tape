Output docs/assets/autolean-pythagorean.gif
Output docs/assets/autolean-pythagorean.mp4

Require autolean

Set Shell "bash"
Set FontSize 16
Set Width 1280
Set Height 1200
Set Framerate 24
Set PlaybackSpeed 4.0
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
Type `export PS1='autolean ❯ ' GIT_PAGER=cat`
Enter
Type `autolean() { command autolean "$@"; status=$?; if test "$status" -eq 0 && test "${1:-}" = prove; then printf '\nDEMO_PROVE_COMPLETE'; read -r _; printf '\033[1A\r\033[2K'; fi; return "$status"; }`
Enter
Type "clear"
Enter
Show

Type `autolean prove "the Pythagorean theorem" --review-plan --guide "State it geometrically: for points p1 p2 p3 in a Euclidean affine space with angle p1 p2 p3 = pi / 2, dist p1 p3 ^ 2 = dist p1 p2 ^ 2 + dist p2 p3 ^ 2. Close with Mathlib's EuclideanGeometry.dist_sq_eq_dist_sq_add_dist_sq_iff_angle_eq_pi_div_two." --max-attempts 5`
Enter
Wait@240s /Use this plan?/
Sleep 4s
Type "y"
Enter
Wait@900s /DEMO_PROVE_COMPLETE/
Enter
Sleep 6s

Type "clear"
Enter
Sleep 1s
Type `autolean sessions`
Enter
Sleep 5s

Type `git -C workspace log -1 --stat`
Enter
Sleep 5s

Type "clear"
Enter
Sleep 1s
Hide
Type `session="$(autolean sessions --json | jq -r '.[0].id')"`
Enter
Show
Type `autolean export pythagorean-artifact --session "$session" --title "A checked Pythagorean theorem"`
Enter
Sleep 8s

Type "clear"
Enter
Sleep 1s
Type `find pythagorean-artifact -maxdepth 3 -type f | sort`
Enter
Sleep 7s

Type `cd workspace && lake env lean AutoLean/Generated/*.lean`
Enter
Sleep 10s
