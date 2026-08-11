Output docs/assets/autolean-pythagorean.gif
Output docs/assets/autolean-pythagorean.mp4

Require autolean

Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 1200
Set Framerate 24
Set PlaybackSpeed 3.0
Set TypingSpeed 25ms
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
Type `autolean() { command autolean "$@"; status=$?; if test "$status" -eq 0 && test "${1:-}" = prove; then printf '\nDEMO_COMPLETE'; read -r _; printf '\033[1A\r\033[2K'; fi; return "$status"; }`
Enter
Type "clear"
Enter
Show

Type `autolean prove "the Pythagorean theorem" --review-plan --max-attempts 3`
Enter
Hide
Wait@60s /Use this plan?/
Show
Sleep 4s
Type "y"
Enter

Wait@300s /DEMO_COMPLETE/
Enter
Sleep 5s
Show
Sleep 5s
Type "clear"
Enter
Sleep 1s

Type `autolean results --file workspace/results.tsv --tail 5`
Enter
Sleep 5s

Type `python -m json.tool workspace/skills/single_simpa.json`
Enter
Sleep 6s

Type "clear"
Enter
Sleep 1s

Type `git -C workspace log -1 --format='accepted commit: %h %s'`
Enter
Sleep 3s

Type `sed -n '/theorem pythagorean_theorem/,$p' workspace/AutoLean/Generated/PythagoreanTheorem.lean`
Enter
Sleep 6s
