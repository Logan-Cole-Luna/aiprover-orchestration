Write the mathematical text of a report that presents a machine-checked proof to a mathematician. The reader knows the source result and wants to check, without reading Lean, what was proved and how.

<informal_statement>
{informal_statement}
</informal_statement>

<informal_proof>
{informal_proof}
</informal_proof>

<lean_solution>
{lean_solution}
</lean_solution>

<declarations>
{declarations}
</declarations>

The Lean code is inserted by the report generator: write \LeanDecl{{name}} where the code of a declaration listed above belongs, and never write Lean code yourself. \LeanDecl{{{theorem_name}}} inserts the target statement.

Structure of <body>:
\section{{Statement}}: the setting and the result as in the source, in clean LaTeX, with the same content.
\section{{Formalization}}: each definition in mathematical terms, followed by its \LeanDecl; then the target statement, \LeanDecl{{{theorem_name}}}, and how it relates to the source statement, including any parameter or hypothesis that stands for an object of the source.
\section{{Proof}}: \subsection{{Overview}} relating the lemmas to the steps of the source proof and stating where the formal proof deviates from it; then every remaining declaration in the listed order, each as
  \begin{{lemma}}[\leanname{{name}}]\label{{lem:name}} statement \end{{lemma}}
  \LeanDecl{{name}}
  \begin{{proof}} argument \end{{proof}}
and finally \subsection{{Proof of the theorem}} with \LeanDecl{{solution}} and its argument.

Declarations marked "library" are verified results the proof builds on, from earlier work: do not present them as lemmas. Introduce the library briefly in the Formalization section (the definitions it provides) and cite its theorems by name where the proof uses them; \LeanDecl of a library declaration is optional.

Requirements:
- Each lemma statement says exactly what its Lean statement says: all hypotheses, quantifiers and types (for example, $s \in \mathbb{{C}}^\times$ for a unit), with the same strength; not the source's informal version.
- Each proof gives the mathematical argument the Lean proof carries out, at the level of a careful paper proof: which earlier lemmas (\cref{{lem:...}}) and which defining relations it uses, and the key computation. Do not narrate tactics.
- Every listed declaration appears exactly once via \LeanDecl.
- Use only amsmath, amssymb, mathtools, amsthm environments (theorem, lemma, definition, remark, proof), itemize and enumerate, \cref, \leanname and \LeanDecl. Put any \newcommand or \DeclareMathOperator lines in <macros>. No Unicode mathematical symbols; no \usepackage, \input or document environment.

Reply with:
<title>Title of the report</title>
<abstract>Three to five sentences: the result, its source, and what was formally verified.</abstract>
<macros>\newcommand lines, or empty</macros>
<body>
The sections above.
</body>