Write a short research proposal, four to six pages, for the expert whose work this project formalizes. No result has been proved in Lean yet; the project has a refereed blueprint. The proposal asks the expert for feedback on the plan before its formalization begins.

<project>
{title}

{goal}
</project>

<blueprint>
{blueprint}
</blueprint>

<open_items>
{open_items}
</open_items>

<report>
{report}
</report>

The report is the refereed plan: its corrections are already applied, and its mathematics is the source of every statement in the proposal. The blueprint is the project's current plan, in dependency order.

Structure of <body>:
\section{{Question}}: one paragraph stating the question the project answers, so that the expert can confirm it was read correctly.
\section{{Approach}}: the setting and the objects chosen to study the question, and why. State plainly every choice the expert has not yet approved, such as synthetic objects in place of those of the expert's papers.
\section{{Main statements}}: each main result of the plan as \begin{{theorem}} or \begin{{proposition}}, labelled, with a proof idea of one paragraph. A statement that the report only conjectures is a \begin{{conjecture}}, never a theorem.
\section{{Roadmap}}: the theorems of the blueprint in dependency order, as an enumerate, each with one line on its content and the theorems it uses, grouped into foundations, general results, and the results specific to the chosen objects.
\section{{Findings}}: what the plan found that bears on the project's hypothesis, including any finding against it, stated first.
\section{{Decisions for the author}}: numbered questions the expert can answer briefly, each with the consequence of each answer; then the conjectures and open points, asking which matter.
\section{{Status and next steps}}: two or three sentences: nothing is machine-verified yet; once the decisions are made, the theorems are formalized one at a time, and each theorem's proof plan can be reviewed before proving.
\appendix\section{{Proofs}}: complete proofs of the main statements, as the report gives them.

Requirements:
- Keep each statement's exact content from the report: hypotheses, quantifiers, scope, and strength.
- Write for a researcher in the field: no discussion of how the plan was produced, of models, costs, referee corrections, simulators, file names, or code identifiers. Experimental observations from the report may be cited as observations.
- Use only amsmath, amssymb, mathtools, amsthm environments (theorem, proposition, lemma, conjecture, definition, remark, proof), itemize, enumerate, and \cref. Put any \newcommand or \DeclareMathOperator lines in <macros>, each defined once. No Unicode mathematical symbols; no \usepackage, \input, or document environment.

Reply with:
<title>Title</title>
<abstract>Three to five sentences: the question, the approach, the main statements, and that feedback is requested before formalization.</abstract>
<macros>\newcommand lines, or empty</macros>
<body>
The sections above.
</body>
