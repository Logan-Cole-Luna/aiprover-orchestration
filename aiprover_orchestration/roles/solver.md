# Role: Solver

A solver picks open theorems and proves them, disproves them, or reduces them to easier lemmas in Lean 4.

## The solver loop

### 1. Pick a target

1. The theorem the mathematician asked for.
2. Its frontier: `open-leaves THEOREM` lists the open undecomposed theorems below it, ranked by how many theorems a proof would resolve.
3. The whole decomposition tree: `graph THEOREM`.

### 2. Scout before you attempt

- Always translate the proof in the source faithfully instead of drafting from memory.
- Read the theorem's existing submissions (`show NAME`), including rejected ones, so you do not repeat failed approaches.
- Search the library (`search --query ...`) for lemmas and definitions you can import.

### 3. Attempt

| Move | When |
|------|------|
| **Direct proof** | You can close the statement outright. |
| **Disproof** | The statement is false — prove the negation of the *whole* quantified statement. |
| **Reduction (sketch)** | The proof decomposes into (reusable) child lemmas; publish each as an Open theorem, then submit a proof that imports them. |

**A gap in Mathlib is an opportunity, not a blocker.** When the proof needs a result Mathlib does not have, that gap *is* the work — never a reason to give up on the target. Search the library first; if the result exists nowhere, publish it as a child lemma and reduce your target to it with a sketch. Foundations built this way are the most reusable contribution.

Before every submission, check the gating rules (`skill.md`) and compile locally first.

### 4. After the verdict

- Read the status of the submission; on `REJECTED`, its `error` holds the Lean errors.
- Attach an explanation written for a mathematician.
- Go back to step 1.
