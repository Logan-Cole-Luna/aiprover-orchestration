Write an informalization of a machine-checked proof: a self-contained mathematical text, for research mathematicians, that states and proves the result through the lemmas of the formal proof, entirely in natural mathematical language. The reader will not read Lean; the text must let them understand every lemma and the whole argument.

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

Structure of <body>:
\section{{Setting}}: the objects of the formalization (the definitions in <lean_source>) in standard notation, as a mathematician would define them, with the same content; then the result as \begin{{theorem}}\label{{thm:main}} ... \end{{theorem}}, stated exactly as the target theorem {theorem_name} states it, with a sentence on how it relates to the source statement if they differ.
\section{{Lemmas}}: every lemma listed in <lemmas>, in the listed order, each as
  \begin{{lemma}}[short descriptive title]\label{{lem:NAME}} statement \end{{lemma}}
  \begin{{proof}} argument \end{{proof}}
where NAME is the lemma's name exactly as listed. Group the lemmas under \subsection headings by their role in the argument if that helps the reader, keeping the order. A lemma marked "not verified" has no machine-checked proof: add \unverified{{}} at the end of its statement and give the argument the formal sketch intends, saying in the proof that it is not yet verified.
\section{{Proof of the theorem}}: the proof of \cref{{thm:main}} from the lemmas, following the main proof in <lean_source> (`solution` or the main proof), citing lemmas by \cref{{lem:NAME}}; then a short paragraph relating the argument to the source proof and stating where it deviates.

Requirements:
- Each statement says exactly what its Lean statement says: all hypotheses, quantifiers and types, with the same strength, in mathematical language (for example, $s \in \mathbb{{C}}^\times$ for a unit). Do not weaken, strengthen or informally paraphrase the scope.
- Each proof is the mathematical argument of its Lean proof at the level of a careful paper proof: the lemmas and defining relations it uses (cited by \cref) and the key computations, written out. Do not narrate tactics and do not mention Lean, tactics or identifiers of the code.
- Use names from mathematics, not from the code; a helper declaration of the Lean source is absorbed into the proof that uses it.
- Use only amsmath, amssymb, mathtools, amsthm environments (theorem, lemma, definition, remark, proof), itemize and enumerate, \cref and \unverified. Put any \newcommand or \DeclareMathOperator lines in <macros>. No Unicode mathematical symbols; no \usepackage, \input or document environment.

Reply with:
<title>Title</title>
<abstract>Three to five sentences: the result, its source, the structure of the proof, and which parts are machine-verified.</abstract>
<macros>\newcommand lines, or empty</macros>
<body>
The sections above.
</body>