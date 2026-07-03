---
description: Write a step-by-step plan file before executing a non-trivial task
argument-hint: <task description>
---

Plan this task before touching any code: $ARGUMENTS

1. Read the relevant existing implementation first (find the closest reference in the codebase).
2. Create `.claude/plans/<kebab-case-task-name>.md` containing:
   - A one-line goal.
   - A complexity marker at the top: ✅ Simple / ⚠️ Medium / 🔴 Complex.
   - The implementation broken into GitHub-style checkboxes: `- [ ] Step description`,
     with sub-bullets for granular detail.
   - **Each step must include at least one validation** (a test, an import check, a curl, etc.).
3. Present the plan and wait for approval before executing.
4. As you complete each step, flip its checkbox `- [ ]` → `- [x]` in the plan file and announce
   which step you are starting next. Only move on after the previous box is checked.

Keep plans detailed enough to execute without ambiguity. If a task is 🔴 Complex, break it into
sub-plans before starting.
