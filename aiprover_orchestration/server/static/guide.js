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
<p>The main page lists all runs, with the most recent first. Figure 3 identifies its principal elements.</p>
${figure("runs.png", 3, "The main page.<br><b>1</b> Submit a problem.<br><b>2</b> Filter runs by outcome.<br><b>3</b> Search by name.<br><b>4</b> Name of a run, which opens its record.<br><b>5</b> Result of the run.")}
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
<p>The replay presents the history of a run as a tree, read from top to bottom: the statement, its formalization, the plan, the lemmas, and the verified proof. As Figure 10 shows, it opens on the completed tree.</p>
${figure("replay.png", 10, "The replay.<br><b>1</b> Completed tree.<br><b>2</b> <b>Successful only</b> omits failed attempts.<br><b>3</b> <b>Play</b> reconstructs the tree step by step.<br><b>4</b> Description of the current step.")}
<p>Selecting any node displays the full record of that step.</p>

<h2 id="guide-incomplete"><span>8</span>Incomplete runs</h2>
<p>An unfinished run retains its proved lemmas. <b>Resume</b>, at the right of the record's tabs, continues the run from the point at which it stopped. The replay identifies the lemmas that remain unproved, and the appropriate response depends on their content.</p>
<ul>
<li><b>A lemma appears to be false.</b> The statement may require an additional hypothesis; a corrected statement should be submitted as a new run.</li>
<li><b>The plan appears unsuitable.</b> A new run with a more detailed informal proof may succeed.</li>
<li><b>The plan appears sound.</b> The run should be resumed.</li>
</ul>
<p>The <b>Unfinished</b> tab of the main page collects these runs.</p>

<h2 id="guide-glossary"><span>9</span>Glossary</h2>
<dl class="terms"><dt>Lean</dt><dd>A language and proof assistant in which mathematics is written so that a computer can verify it.</dd>
<dt>Mathlib</dt><dd>The shared library of formalized mathematics on which these proofs build.</dd>
<dt>Formal statement</dt><dd>A theorem as written in Lean.</dd>
<dt>Formalization</dt><dd>The translation of mathematics into Lean.</dd>
<dt>Orchestrator</dt><dd>The agent that formalizes the problem, plans the proof, and resolves difficulties.</dd>
<dt>Auditor</dt><dd>The agent that checks a formalization without access to the source text.</dd>
<dt>Solver</dt><dd>A model, AIProver, that proves individual lemmas.</dd>
<dt>Sorry</dt><dd>Lean's placeholder for a missing proof. A proved run contains none.</dd>
<dt>Hand-back</dt><dd>The return of a lemma from the solvers to the orchestrator after repeated failure.</dd></dl>`;
