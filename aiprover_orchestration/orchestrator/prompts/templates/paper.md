Write a research paper from a group of machine-checked results on one subject. Each result below comes with its informalization: a mathematical text, already checked against its Lean proof, that states the result and proves it through the lemmas of the formal proof. The paper presents all of them as one coherent, self-contained article for research mathematicians, suitable for submission to a journal.

<title_hint>
{title}
</title_hint>

<results>
{results}
</results>

Each result has a role: "background" results are established facts on which the paper builds, presented in a preliminaries section with their full statements and a concise account of their proofs; "main" results are the paper's contributions, presented with their full statements, every lemma, and complete proofs.

Structure of <body>:
\section{{Introduction}}: the objects studied, the main results stated informally with forward references (\cref{{thm:SLUG}}), and how they relate to one another and to the background results. State that every result is machine-verified.
\section{{Preliminaries}}: one unified setting for all results: the definitions, notation and presentations, each given once, in standard notation, consistent with every informalization; then the background results, each as \begin{{theorem}}\label{{thm:SLUG}} ... \end{{theorem}}.
One section per main result, in the given order: the theorem as \begin{{theorem}}\label{{thm:SLUG}} ... \end{{theorem}}, its lemmas each as \begin{{lemma}}[short descriptive title]\label{{lem:SLUG:NAME}} ... \end{{lemma}} with its proof, and the proof of the theorem from the lemmas. Where a result uses an earlier one, cite it by \cref instead of proving it again; a lemma that only restates an earlier result is replaced by that citation.
\section{{Conclusion}}: what the results establish together, and the questions they leave open, without speculation beyond the texts.

Requirements:
- SLUG is the result's slug and NAME a lemma's name exactly as given in <results>; each main result's lemmas keep their names.
- Every statement keeps the exact content of its informalization: hypotheses, quantifiers, scope and strength. Unify notation across results, but do not weaken, strengthen or paraphrase a statement's scope.
- Proofs follow their informalizations at the level of a careful paper proof, citing earlier results and lemmas by \cref.
- The paper reports mathematics only: no discussion of how the results were found, of failed attempts, of errors in sources or earlier drafts, of Lean, tactics or code identifiers.
- Use only amsmath, amssymb, mathtools, amsthm environments (theorem, lemma, definition, remark, proof), itemize and enumerate, and \cref. Put any \newcommand or \DeclareMathOperator lines in <macros>, each defined once. No Unicode mathematical symbols; no \usepackage, \input or document environment.

Reply with:
<title>Title</title>
<abstract>Four to six sentences: the objects, the main results, the role of the background results, and that every result is machine-verified.</abstract>
<macros>\newcommand lines, or empty</macros>
<body>
The sections above.
</body>
