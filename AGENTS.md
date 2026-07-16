# Architecture

Refer to @docs/architecture.md to understand the system architecture.

# Quality
When coding, you must always use a git-work-tree to develop features and bug fixes.

You MUST spawn a subagent to run the verification following the instruction in @docs/quality.md before concluding your job done.

If your subagent reports back any error, failure, warning, you will fix it.

Repeat until your code passes subagent's verification, then you are allowed to open a pull request


# CI/CD

When a PR is submitted to Github, you will wait for github action to execute the tests until you receive results. If tests pass, your PR is accepted, and your job is done, otherwise, you must gather the failure details and fix them, repeat the coding -> static checking -> ci testing again.
