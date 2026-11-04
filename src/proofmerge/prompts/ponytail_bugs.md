This review also looks for bugs. For this review the exclusion of correctness bugs above is lifted
only as described here. Report them in the separate "bugs" list, never in "findings".

A bug is a line the diff adds that will do the wrong thing for an input you can name. For each one
give "location" (path and head-side line range), a "tag", "what" (the input that breaks it and what
goes wrong, one sentence) and "fix" (the corrected line or approach, one sentence).

Tags:
- logic: a wrong condition, an off-by-one, a wrong return value, a missing await, a mixed-up argument.
- edge-case: an empty, missing, zero or very large input the new code does not handle.
- error: an error that is swallowed or reported wrongly, or cleanup that is skipped.
{high}
Rules for bugs:
- You are reading a diff and running nothing, so report only what you can point to on a specific added line. If you are unsure, leave it out.
- Style, naming, performance and anything the pull request did not add are still out of scope.
- At most {limit} bugs, the most likely first. If there are none, return an empty "bugs" list.
