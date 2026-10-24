Write an informalization of a machine-checked disproof: a self-contained mathematical text, for research mathematicians, that states the claim of the source and proves its negation, entirely in natural mathematical language. The reader will not read Lean; the text must let them understand why the claim fails.

<informal_statement>
{informal_statement}
</informal_statement>

<informal_proof>
{informal_proof}
</informal_proof>

<lean_source>
{lean_source}
</lean_source>

<lemmas>
{lemmas}
</lemmas>

In <lean_source>, the target theorem states the source's claim and is left unproved; the theorem {theorem_name} proves its negation, from the lemmas listed in <lemmas>.

Structure of <body>:
\section{{Setting}}: the objects of the formalization (the definitions in <lean_source>) in standard notation, as a mathematician would define them, with the same content; then the source's claim in a \begin{{remark}}[The claim] ... \end{{remark}}, stated exactly as the target theorem states it; then the result as \begin{{theorem}}\label{{thm:main}} ... \end{{theorem}}: the negation of the claim, stated exactly as {theorem_name} states it.
\section{{Lemmas}}: every lemma listed in <lemmas>, in the listed order, each as
  \begin{{lemma}}[short descriptive title]\label{{lem:NAME}} statement \end{{lemma}}
  \begin{{proof}} argument \end{{proof}}
where NAME is the lemma's name exactly as listed. Leave this section out when <lemmas> is empty.
\section{{Proof of the theorem}}: the proof of \cref{{thm:main}}, following the proof of {theorem_name} in <lean_source> and citing lemmas by \cref{{lem:NAME}}; then a short paragraph on what the disproof shows about the source's setting: the obstruction it exhibits and which relations or hypotheses it rests on.

Requirements:
- Each statement says exactly what its Lean statement says: all hypotheses, quantifiers and types, with the same strength, in mathematical language (for example, $s \in \mathbb{{C}}^\times$ for a unit). Do not weaken, strengthen or informally paraphrase the scope.
- Each proof is the mathematical argument of its Lean proof at the level of a careful paper proof: the lemmas and defining relations it uses and the key computations, written out; a check over all defining relations is written out relation by relation. Do not narrate tactics and do not mention Lean, tactics or identifiers of the code.
- Use names from mathematics, not from the code; a helper declaration of the Lean source is absorbed into the proof that uses it.
- Use only amsmath, amssymb, mathtools, amsthm environments (theorem, lemma, definition, remark, proof), itemize and enumerate, \cref and \unverified. Put any \newcommand or \DeclareMathOperator lines in <macros>. No Unicode mathematical symbols; no \usepackage, \input or document environment.

Reply with:
<title>Title</title>
<abstract>Three to five sentences: the claim, its source, why it fails, and that the disproof is machine-verified.</abstract>
<macros>\newcommand lines, or empty</macros>
<body>
The sections above.
</body>
