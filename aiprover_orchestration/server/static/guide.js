// The user guide of the query server, written for mathematicians new to Lean.
const plain = caption => caption.replace(/<[^>]+>/g, "");
const diagramFigure = (content, number, caption) => `<figure>${content}<figcaption><b>Figure ${number}.</b> ${caption}</figcaption></figure>`;
const figure = (file, number, caption) => diagramFigure(`<img data-themed-image="${file}" alt="${plain(caption)}">`, number, caption);

// The two diagrams are drawn here so that they follow the page's theme.
const box = (x, y, width, height, kind, title, ...lines) => {
  const top = y + height / 2 - (lines.length * 16 - 6) / 2;
  return `<rect class="${kind}" x="${x}" y="${y}" width="${width}" height="${height}" rx="6"/>
    <text class="head" x="${x + width / 2}" y="${top}">${title}</text>`
    + lines.map((line, i) => `<text x="${x + width / 2}" y="${top + 18 + i * 16}">${line}</text>`).join("");
};
const arrow = (d, kind = "") => `<path class="${kind}" d="${d}" marker-end="url(#arrowhead)"/>`;
const gate = (x, label) => `<rect class="gate" x="${x}" y="0" width="86" height="25" rx="4"/><text class="gate-text" x="${x + 43}" y="17">${label}</text>`;
const label = (x, y, text) => `<text x="${x}" y="${y}">${text}</text>`;
const diagram = (height, title, body) => `<svg class="diagram" viewBox="0 0 900 ${height}" role="img" aria-label="${title}">
  <defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z"/></marker></defs>${body}</svg>`;
const sessions = [0, 1].flatMap(row => [0, 1, 2, 3, 4, 5].map(column =>
  `<rect class="session ${[0, 5, 10].includes(row * 6 + column) ? "done" : ""}" x="${477 + column * 34}" y="${82 + row * 33}" width="25" height="23" rx="3"/>`)).join("");
const ARCHITECTURE = diagram(170, "System architecture",
  box(0, 50, 143, 72, "", "Informal problem")
  + arrow("M144 86H182")
  + box(183, 50, 215, 72, "orchestrator", "Orchestrator", "plans and assembles")
  + label(425, 62, "lemmas") + arrow("M399 72H452") + arrow("M452 100H399") + label(425, 122, "proofs")
  + `<rect class="solver" x="453" y="8" width="237" height="156" rx="6"/><text class="head" x="571" y="46">AIProver solvers</text>` + sessions
  + arrow("M691 86H729")
  + box(730, 50, 170, 72, "proved", "Verified proof"));
const stages = [["Formalize", ""], ["Blind audit", ""], ["Sketch", ""], ["Prove", "solver"], ["Assemble", ""], ["Review", ""], ["Report", ""]];
const PIPELINE = diagram(190, "Stages of a run",
  stages.map(([title, kind], i) => box(i * 128 + 4, 40, 108, 56, kind, title)).join("")
  + [0, 1, 2, 3, 4, 5].map(i => arrow(`M${i * 128 + 113} 68H${i * 128 + 131}`)).join("")
  + gate(143, "FAITHFUL") + gate(527, "0 sorry") + gate(655, "FAITHFUL")
  + arrow("M186 97V122H58V98", "loop") + label(122, 138, "revise")
  + arrow("M442 97V122H314V98", "loop") + label(378, 138, "replan")
  + arrow("M698 97V160H314V98", "loop") + label(506, 176, "replan"));
const PROJECT = diagram(210, "Blueprint of an example project",
  box(0, 8, 200, 58, "proved", "Bezout", "2 of 2 lemmas")
  + box(0, 142, 200, 58, "proved", "PrimeFactorization", "3 of 3 lemmas")
  + box(330, 8, 200, 58, "proved", "EuclidLemma", "1 of 1 lemmas")
  + box(660, 75, 240, 58, "", "UniqueFactorization", "Interrupted at 4 of 7")
  + arrow("M201 37H329") + arrow("M531 37H600V90H659") + arrow("M201 171H600V118H659"));
const GUIDE_HTML = `
<h1>User guide</h1>
<p class="lede">This system formalizes mathematical results stated in natural language and verifies their proofs in the Lean 4 proof assistant. Its use requires no programming experience and no prior knowledge of Lean.</p>

<h2 id="guide-overview"><span>1</span>Overview</h2>
<p>A user supplies a statement and, optionally, an informal proof. A group of AI agents then proceeds through the following stages.</p>
<ol>
<li><b>Formalization.</b> The orchestrator translates the statement into Lean. An auditor, which does not see the source text, reconstructs the statement from the translation, and the two are compared.</li>
<li><b>Planning.</b> The orchestrator decomposes the proof into a sequence of lemmas.</li>
<li><b>Proof.</b> Provers address the lemmas in parallel. A lemma that resists repeated attempts returns to the orchestrator, which may prove it, divide it, or revise the plan.</li>
<li><b>Assembly.</b> The lemmas are combined into a single proof, which Lean verifies in full.</li>
<li><b>Review and reporting.</b> An independent reviewer compares the verified proof with the source statement, and the system then prepares a report in mathematical language.</li>
</ol>
${diagramFigure(ARCHITECTURE, 1, "System architecture. The orchestrator assigns lemmas to a pool of solvers. Green squares denote successful sessions.")}
<p>Figure 2 presents these stages in order. Each green label denotes a check that must pass before the run proceeds, and each dashed arrow denotes the stage to which a failed check returns.</p>
${diagramFigure(PIPELINE, 2, "Stages of a run. A report is produced only after the proof passes Lean and both faithfulness checks.")}
<p class="callout"><b>Scope of verification.</b> Lean establishes that the proof is correct for the formal statement. It cannot establish that the formal statement expresses the intended theorem; the agents assess this correspondence, and their assessment may be mistaken. Consequently, the formal statement in the report should be read directly before a result is relied upon.</p>

<h2 id="guide-submission"><span>2</span>Submitting a problem</h2>
<p>The home page leads to each section and shows the run that most needs attention. The <b>Runs</b> page lists all runs, with the most recent first. Figure 3 identifies its principal elements.</p>
${figure("runs.png", 3, "The Runs page.<br><b>1</b> Submit a problem.<br><b>2</b> Filter runs by outcome.<br><b>3</b> Search by name.<br><b>4</b> Name of a run, which opens its record.<br><b>5</b> Result of the run.")}
<h3>Step 1: Problem</h3>
<p>Select <b>Submit a problem</b>, then enter the statement and, where available, an informal proof. LaTeX notation is accepted, as Figure 4 illustrates. The run name is optional and serves to identify the run later.</p>
<div class="example"><p><b>Run name:</b> sum_of_odd_numbers</p>
<p><b>Statement:</b> For every positive integer $n$, the sum of the first $n$ odd positive integers equals $n^2$; that is, $\\sum_{k=1}^{n} (2k-1) = n^2$.</p>
<p><b>Informal proof:</b> By induction on $n$. For $n=1$ both sides equal 1. If the claim holds for $n$, adding $2(n+1)-1 = 2n+1$ to both sides gives $n^2 + 2n + 1 = (n+1)^2$.</p></div>
${figure("submit.png", 4, "The first step of a submission, completed with the example above.")}
<p>The optional <b>Group</b> field associates related problems. A user may select an existing group, create a new group, or leave the field empty. Groups are specific to each server. On the main page, <b>Sort by</b> arranges runs by date or by group, and any date or group may be collapsed by selecting its heading.</p>
<h3>Step 2: Library, models, and settings</h3>
<p>The default values are suitable for most runs, and these steps may therefore be left unchanged.</p>
<h3>Step 3: Review</h3>
<p>The final step summarizes the submission, as Figure 5 shows. After <b>Submit</b> is selected, the run enters the queue and continues independently of the page.</p>
${figure("review.png", 5, "The review step, which precedes submission.")}

<h2 id="guide-formulation"><span>3</span>Formulating a statement</h2>
<p>The agents interpret a statement literally. Consequently, an omission that a colleague would resolve from context may lead to an unintended formalization.</p>
<table class="compare"><tr><th>Ambiguous</th><th>Precise</th></tr>
<tr><td>Let $G$ be a group. Then $G$ has a normal subgroup of index 2.</td><td>Let $G$ be a finite group of order $2m$ with $m$ odd. Then $G$ has a normal subgroup of index 2.</td></tr>
<tr><td>Let $f$ be continuous. Then $f$ attains its maximum.</td><td>Let $f : [0,1] \\to \\mathbb{R}$ be continuous. Then there is $x \\in [0,1]$ with $f(y) \\le f(x)$ for all $y \\in [0,1]$.</td></tr></table>
<ul>
<li><b>State every hypothesis,</b> including those conventional in the field.</li>
<li><b>Define specialized terms and notation.</b> Standard objects require no definition, whereas a notion introduced in a particular paper does.</li>
<li><b>Provide a proof where one exists,</b> even in outline, since it guides the plan.</li>
<li><b>Identify cited results</b> and state their sources.</li>
</ul>

<h2 id="guide-monitoring"><span>4</span>Monitoring a run</h2>
<p>A run typically requires several hours, and difficult problems may require days. The page need not remain open.</p>
<p>Selecting the name of a run opens its record. The <b>Progress</b> tab, shown in Figure 6, lists the steps of the run in chronological order.</p>
${figure("progress.png", 6, "An open run.<br><b>1</b> Tabs of the record.<br><b>2</b> Progress log, here from the final lemma through assembly, review, and reporting.")}

<h2 id="guide-results"><span>5</span>Interpreting results</h2>
<p>Each run concludes with one of the following results.</p>
<table class="compare"><tr><th>Result</th><th>Meaning</th></tr>
<tr><td class="good">∎ Proved</td><td>Lean has verified a complete proof of the formal statement.</td></tr>
<tr><td class="good">¬ Disproved</td><td>Lean has verified a proof of the negation. The hypotheses should be examined, since an omitted hypothesis can produce this result.</td></tr>
<tr><td class="warn">○ Lemmas unproved</td><td>Some lemmas of the plan remain without proofs.</td></tr>
<tr><td class="bad">× Failed</td><td>The run ended before reaching a proof.</td></tr>
<tr><td class="quiet">/ Cancelled</td><td>The run was stopped by a user.</td></tr></table>
<p>The <b>Files</b> tab, shown in Figure 7, describes each output of the run. The informal proof is the recommended starting point, and the report is the appropriate place to compare the formal statement with the intended theorem.</p>
${figure("files.png", 7, "The Files tab of a proved run.")}
<p>Figure 8 reproduces the first page of each document for one proved run. The informal proof states the theorem and every lemma in natural language, whereas the report sets each statement and proof beside its Lean code. Figure 9 compares the two treatments of a single lemma.</p>
<figure><div class="pages">
<a href="/static/guide/sample/informal_proof.pdf" target="_blank"><img src="/static/guide/sample/informal_proof.png" alt="First page of an informal proof"></a>
<a href="/static/guide/sample/report.pdf" target="_blank"><img src="/static/guide/sample/report.png" alt="First page of a report"></a>
</div><figcaption><b>Figure 8.</b> First pages of the <a href="/static/guide/sample/informal_proof.pdf" target="_blank">informal proof</a> (left) and the <a href="/static/guide/sample/report.pdf" target="_blank">report</a> (right) of OpenAI math problem 011, a conditional form of a conjecture of Erdős on large totient fibers. The remaining figures of this section and the next follow the same run. <span class="screen-only">Select a page to open the complete document.</span></figcaption></figure>
<figure><div class="pages"><img src="/static/guide/sample/lemma_informal.png" alt="A lemma as it appears in the informal proof"><img src="/static/guide/sample/lemma_report.png" alt="The same lemma as it appears in the report"></div><figcaption><b>Figure 9.</b> One lemma in both documents: the informal proof (left) and the report (right), where the Lean proof follows the statement.</figcaption></figure>
<p>The <b>Details</b> tab records the dates of the run and the models that it used. This information should accompany any citation of the result.</p>

<h2 id="guide-lean"><span>6</span>The Lean file</h2>
<p>The Lean file contains the complete formal proof and can be checked independently by anyone with Lean. It is intended for a proof assistant rather than for a reader: the file of this run has 1,135 lines, and Lean has already verified each of its proofs. Consequently, a reader need only examine the final theorem and the definitions on which it depends.</p>
<pre class="box"><code>theorem solution
    (hH : SmoothShiftedPrimesHypothesis) (ε : ℝ) (hε : 0 &lt; ε) :
    Set.Infinite {n : ℕ | 0 &lt; n ∧ ((totientFiberCard n : ℕ) : ℝ) &gt; (n : ℝ) ^ (1 - ε)}</code></pre>
<p>This statement asserts that for every $\\varepsilon &gt; 0$ there are infinitely many positive integers $n$ with $g(n) &gt; n^{1-\\varepsilon}$, where $g(n)$ counts the solutions $m$ of $\\varphi(m) = n$. The assumption <code>hH</code> illustrates why the statement merits attention: the hypothesis on smooth shifted primes is assumed rather than proved, and the theorem is therefore conditional on it. A proved run contains no <code>sorry</code>, and the report lists the axioms on which the proof relies.</p>

<h2 id="guide-replay"><span>7</span>The replay</h2>
<p>The replay presents the history of a run as a tree, read from top to bottom: the statement, its formalization, the plan, the lemmas, and the verified proof. As Figure 10 shows, it opens on the completed tree. The <b>Replay</b> tab of a run shows it within the record; <b>Open page</b> opens it on a page of its own, and <b>Full screen</b> enlarges it to the whole screen, and <b>Exit full screen</b> returns it to the record.</p>
${figure("replay.png", 10, "The replay.<br><b>1</b> Completed tree.<br><b>2</b> <b>Successful only</b> omits failed attempts.<br><b>3</b> <b>Play</b> reconstructs the tree step by step.<br><b>4</b> Description of the current step.")}
<p>Selecting any node displays the full record of that step.</p>

<h2 id="guide-blueprint"><span>8</span>Blueprints</h2>
<p>A blueprint presents a proof plan as a graph of statements, in which an arrow leads from each result to the results that use it. The <b>Blueprint</b> tab of a run shows the lemmas of its plan and marks each as planned, proved, or refuted. Selecting a lemma displays its statement in mathematical language, the idea of its proof, its Lean statement, and the lemmas that depend on it. <b>Outline</b> lists the same lemmas as text, and the blueprint can be downloaded in the LaTeX format of the leanblueprint package. <b>Full screen</b> enlarges the blueprint to the whole screen, which suits a large plan; <b>Exit full screen</b> or the Escape key returns it to the page.</p>
${figure("blueprint.png", 11, "The Blueprint tab of the run of Figure 6, with <code>exists_good_n_large</code> selected. The graph highlights the lemma, the three lemmas it uses, and the lemma that uses it; the panel gives its statement, proof idea, and Lean statement.")}
<p>A blueprint is updated as lemmas are proved. A plan is regarded as sound when each open lemma is plausible and its dependencies are appropriate, irrespective of how many lemmas have yet been proved.</p>
<p>The setting <b>Require review of the proof plan before proving</b> pauses a run before proving commences, so that a reader may approve the plan, approve it with comments, request a revision, or reject it. The run holds no computing resources during this pause.</p>
<p>A run imported from elsewhere may show Lean statements alone, since its natural-language text was not recorded.</p>

<h2 id="guide-projects"><span>9</span>Projects</h2>
<p>A run addresses one result. A project addresses a body of connected results: the results are proved in dependency order on one shared Lean library, so that each may use those before it. A single theorem belongs in a run; a paper, or a theorem that requires several substantial preliminaries, belongs in a project.</p>
<p>Figure 12 shows the page of a project, with each of its controls numbered.</p>
${figure("projects.png", 12, "The page of a project, here the bounded-arithmetic results of the Jiatu Book."+ "<br><b>1</b> <b>Start a project</b> opens a new project."+ "<br><b>2</b> Results in dependency order, with status, lemmas, and cost; selecting one opens its run."+ "<br><b>3</b> <b>Add result</b> writes a result by hand."+ "<br><b>4</b> <b>Plan milestones</b> divides the goal into results."+ "<br><b>5</b> <b>Research</b> puts a question to the project's model about a stalled result."+ "<br><b>6</b> <b>Write paper</b> composes one article from the verified results."+ "<br><b>7</b> <b>Blueprint</b> shows the results as a graph."+ "<br><b>8</b> The Lean library and source files that the results share.")}
<h3>Starting a project</h3>
<p>Select <b>Submit a problem</b>, then <b>Project</b> in place of <b>Single problem</b>. Enter a title, the goal, and the Lean version, GPU group, and prover that its runs will use. The orchestrator may be Claude or Codex, and the prover may be AIProver, Claude, Codex, or a server of your own. The project receives a new, empty library.</p>
<h3>Signing in</h3>
<p>A submission that uses Claude or Codex without a login opens that service's sign-in, which links to its own site. Claude shows a code to paste back into this page, and Codex shows a code here to enter on OpenAI's site. An API key is the alternative to either. The login stays in your own account on that machine, and this page does not keep a sign-in code.</p>
<h3>Your own model server</h3>
<p><b>Model servers</b>, on the Submit page, adds any server with an OpenAI-compatible API. Enter its address, an API key if it needs one, and its kind: <b>AIProver</b> runs the AIProver harness on the server, and <b>General model</b> treats it as a chat model for any role. The server is checked before it is added, and it must answer from the machine that runs this page. Choose group <code>cpu</code> for a run that needs no shared model server. A key is stored on that machine and is never shown again.</p>
<h3>Source files</h3>
<p>A paper or notes may be attached as one file or as a zip of its files. LaTeX and other text files serve best, PDF files are harder to search, and office documents should first be converted to PDF or text. A short table of contents helps a large collection. An upload may hold up to 256 MB.</p>
<p>Source files are unverified reference material. <b>Plan milestones</b>, <b>Research</b>, and <b>Ask Claude</b> read them, and the orchestrator of each run may open them. The solvers do not receive them, and <b>Write paper</b> uses only verified results. The orchestrator decides whether to read a file, so text that a run must use belongs in the goal or in the statement of a result. Uploaded files neither create results nor add to the library, and a Lean file among them cannot be imported by a run. Each file may be read from the project's <b>Shared by the results</b> section.</p>
<p>As an example, consider a project with the goal of proving that every integer greater than 1 factors uniquely into primes. The sections below follow this project.</p>
<h3>Planning the results</h3>
<p><b>Plan milestones</b> asks the project's model to divide the goal into results, using the source if one is attached, and a second session referees the proposal. The example might receive four results: Bezout's identity, Euclid's lemma, the existence of a prime factorization, and uniqueness. <b>Background</b> marks an established result on which the others build, and <b>main</b> marks a contribution of the project.</p>
<p>Nothing runs until <b>Start remaining results</b> is selected. Until then, each result may be selected and its name, statement, informal proof, and dependencies edited; it may also be moved or removed. <b>Add result</b> writes one by hand. The dependency order deserves particular care, since it reflects the model's reading of the source.</p>
<h3>Following the project</h3>
<p>The table lists each result with its status, lemmas, and cost. Selecting a result opens the record of its run in place, with the tabs of Section 4. The <b>Blueprint</b> button shows the project as a graph of its results, as Figure 13 shows. Each result that has a run offers its own blueprint of lemmas, so that the project may be read at either level.</p>
${diagramFigure(PROJECT, 13, "Blueprint of the example project. Green results are proved. A result may use those from which an arrow leads to it.")}
<p><b>Shared by the results</b> lists what the runs have in common: the Lean library, which grows as each result is verified, and the source files.</p>
<h3>Research on open lemmas</h3>
<p>When a result stalls, <b>Research</b> puts a question to the project's model within a spending limit, and a second session referees the answer. The report may add <b>open lemmas</b>, which remain unproved, and <b>claims</b>, on which the project relies without a Lean proof. To use a report, select <b>Resume</b> on the stalled run and choose the report under <b>Project research</b>; the run then replans with the findings and retains its proved lemmas.</p>
<p class="callout"><b>Reports are not proofs.</b> A research report guides the plan, and every result must still pass Lean. An open lemma is never assumed.</p>
<h3>Reviewing claims</h3>
<p>Review of a claim is optional, and a project does not require an expert. <b>Confirm</b> and <b>Reject</b> record a person's decision. <b>Ask Claude</b> records an independent opinion, which is not a proof and is shown apart from any person's decision. <b>Prove in Lean</b> adds the claim to the project as a result, and is the only choice that settles it.</p>
<p>Figure 14 shows these sections for the example project.</p>
${figure("claims.png", 14, "Open lemmas and claims of the example project." + "<br><b>1</b> <b>Open lemmas</b>: statements that remain unproved, each with its reason; a run never assumes one." + "<br><b>2</b> <b>Claims without a Lean proof</b>: statements on which the project relies without a Lean proof." + "<br><b>3</b> Review status: a person's decision and, once requested, an automated check." + "<br><b>4</b> <b>Confirm</b> records that a person accepts the claim." + "<br><b>5</b> <b>Reject</b> records that a person rejects it." + "<br><b>6</b> <b>Ask Claude</b> requests an independent check, which is not a proof." + "<br><b>7</b> <b>Prove in Lean</b> adds the claim to the project as a result, the only choice that settles it.")}
<h3>Writing the paper</h3>
<p>Once each result has its documents, which <b>Generate documents</b> on a run produces, <b>Write paper</b> composes one article from them and checks it against the Lean statements.</p>

<h2 id="guide-incomplete"><span>10</span>Incomplete runs</h2>
<p>An unfinished run retains its proved lemmas. <b>Resume</b>, at the right of the record's tabs, continues the run from the point at which it stopped. The replay identifies the lemmas that remain unproved, and the appropriate response depends on their content.</p>
<ul>
<li><b>A lemma appears to be false.</b> The statement may require an additional hypothesis; a corrected statement should be submitted as a new run.</li>
<li><b>The plan appears unsuitable.</b> A new run with a more detailed informal proof may succeed.</li>
<li><b>The plan appears sound.</b> The run should be resumed.</li>
</ul>
<p>The <b>Unfinished</b> tab of the main page collects these runs.</p>

<h2 id="guide-glossary"><span>11</span>Glossary</h2>
<dl class="terms"><dt>Lean</dt><dd>A language and proof assistant in which mathematics is written so that a computer can verify it.</dd>
<dt>Mathlib</dt><dd>The shared library of formalized mathematics on which these proofs build.</dd>
<dt>Formal statement</dt><dd>A theorem as written in Lean.</dd>
<dt>Formalization</dt><dd>The translation of mathematics into Lean.</dd>
<dt>Orchestrator</dt><dd>The agent that formalizes the problem, plans the proof, and resolves difficulties.</dd>
<dt>Auditor</dt><dd>The agent that checks a formalization without access to the source text.</dd>
<dt>Solver</dt><dd>A model, AIProver, that proves individual lemmas.</dd>
<dt>Sorry</dt><dd>Lean's placeholder for a missing proof. A proved run contains none.</dd>
<dt>Hand-back</dt><dd>The return of a lemma from the solvers to the orchestrator after repeated failure.</dd>
<dt>Blueprint</dt><dd>A proof plan shown as a graph of statements and their dependencies.</dd>
<dt>Project</dt><dd>Connected results proved in dependency order on one shared library.</dd>
<dt>Milestone</dt><dd>One result of a project, proved by its own run.</dd>
<dt>Claim</dt><dd>A statement on which a project relies without a Lean proof.</dd>
<dt>Open lemma</dt><dd>A statement that a project needs and that remains unproved; it is recorded with its reason and never assumed.</dd></dl>`;
