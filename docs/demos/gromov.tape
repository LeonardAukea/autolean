Output docs/assets/autolean-gromov.gif
Output docs/assets/autolean-gromov.mp4

Require autolean
Require bash
Require codex
Require clear

Source docs/demos/style.tape
Set FontSize 16

Hide
Type `cd "$AUTOLEAN_DEMO_ROOT"`
Enter
Type `export PS1='autolean ❯ ' GIT_PAGER=cat`
Enter
Type "clear"
Enter
Wait@120s /^autolean ❯ *$/
Show
Type "autolean workbench"
Hide
Enter
Wait+Screen@120s /Proof targets/
Show
Sleep 3s
Type "/"
Type "residually_finite_of_finite_quotients"
Sleep 2s
Ctrl+S
Sleep 2s
Tab
Enter
Hide
Wait+Screen@600s /Querying gpt-6-astra/
Show
Sleep 4s
Screenshot .autolean/demo-diagnostics/gromov-working.png
Sleep 1s
Hide
Wait+Screen@2500s /Run finished/
Show
Sleep 9s
Screenshot .autolean/demo-diagnostics/gromov-support.png
Sleep 1s
Ctrl+Q
Hide
Wait@120s /^autolean ❯ *$/
Show
Type "autolean workbench"
Hide
Enter
Wait+Screen@120s /Proof targets/
Show
Sleep 2s
Type "/"
Type "residual_finiteness_question"
Sleep 2s
Ctrl+S
Sleep 2s
Tab
Enter
Hide
Wait+Screen@600s /Querying gpt-6-astra/
Show
Sleep 5s
Hide
Wait+Screen@2500s /Run finished/
Show
Sleep 10s
Screenshot .autolean/demo-diagnostics/gromov-open.png
Sleep 1s
Ctrl+Q
Sleep 1s
