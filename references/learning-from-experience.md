# How a lesson survives: what postmortems, retrospectives, checklists and Anthropic's own agent guidance actually say

*Researched 2026-09-14 by claude-code/sonnet-5. Primary sources only, fetched by URL (WebFetch/curl) and Europe PMC
where a page blocked. "Read": **verbatim** = page text quoted directly; **pdf** = text layer of the primary-source
PDF; **abstract** = the paper's own published abstract, mirrored by a bibliographic aggregator, not the full text;
**not reached** = no primary text obtained this session — nothing below attributes a claim to that source beyond its
existing at that URL.*

## 1. Sources

| Source | URL | Read |
|---|---|---|
| Google, *SRE Book* — "Postmortem Culture: Learning from Failure"; "Managing Incidents" | sre.google/sre-book/postmortem-culture/, …/managing-incidents/ | verbatim |
| Etsy, *2016 Debriefing Facilitation Guide* (Allspaw et al.) | extfiles.etsy.com/DebriefingFacilitationGuide.pdf | pdf |
| Adaptive Capacity Labs — "How Learning Is Different Than Fixing"; "Markers of Progress in Incident Analysis" | adaptivecapacitylabs.com/2020/05/06/…, …/2019/11/20/… | verbatim |
| Agile Manifesto — *Twelve Principles* | agilemanifesto.org/principles.html | verbatim |
| DORA — *Generative Organizational Culture*, *Learning Culture* capability pages | dora.dev/capabilities/generative-organizational-culture/, …/learning-culture/ | verbatim |
| DORA — 2019 report landing pages | dora.dev/research/2019/, …/dora-report/ | not reached (no N/method surfaced; report PDF not opened) |
| Haynes, Weiser, … Gawande, *A Surgical Safety Checklist…*, NEJM 2009 | doi.org/10.1056/NEJMsa0810119, mirrored via europepmc.org | abstract |
| Toyota Motor Corporation — "Jidoka" | global.toyota/en/company/vision-and-philosophy/production-system/ | verbatim |
| US Army — After Action Review doctrine (TC 25-20 / ADP 7-0) | armypubs.army.mil (404 on every candidate path) | not reached |
| Argyris, "Double Loop Learning in Organizations," HBR Sept 1977 | hbr.org/1977/09/double-loop-learning-in-organizations | not reached (paywalled; page/title confirmed only) |
| Nonaka, SECI model primary paper | — | not reached (every mirror tried 404'd or blocked) |
| Anthropic Engineering — "Writing tools for agents" | anthropic.com/engineering/writing-tools-for-agents | verbatim |
| Claude Code docs — "Skills" | code.claude.com/docs/en/skills | verbatim |

## 2. Postmortems and incident learning

SRE book: "Blameless postmortems are a tenet of SRE culture. For a postmortem to be truly blameless, it must focus
on identifying the contributing causes of the incident without indicting any individual or team for bad or
inappropriate behavior," assuming "everyone involved… had good intentions and did the right thing with the
information they had." Contents: "a written record of an incident, its impact, the actions taken to mitigate or
resolve it, the root cause(s), and the follow-up actions to prevent the incident from recurring." On action items it
says only that drafts are reviewed for "resulting bug fixes at appropriate priority" — **it does not discuss
tracking or completion**. "Managing Incidents" adds nothing beyond "Retain this documentation for postmortem
analysis."

Etsy's guide states the finding this research was looking for as its own section heading: **"The Goal Is to Learn,
Not to Produce Remediation Items."** Under it: "The problem comes when the pressure to fix outweighs the pressure to
learn," and "the goal of a debriefing is not to produce recommendations or remediation items… The goal is to seize
the opportunity for an organization to learn." Its fix: hold remediation ideas until the timeline is agreed, give
them "soak time" — "two or so (work) days later, bring that group into a room to discuss the viability" — before
SMART tickets, and "having remediation items is not a requirement for a good debriefing." Etsy's own claim that
"blameless postmortems drive a significant percentage of our development" (Etsy, Inc., 2015) is asserted, not
quantified.

Adaptive Capacity Labs names the failure mode precisely: **"The number of 'orphan' post-incident 'action items' (in
JIRA or other task-tracking systems) will trend downward"** is offered as a sign of progress, implying orphaning is
the default. And: "The challenge is not getting people to learn. The problem is making it easy for people to learn
what is likely to be useful." Separately: fixing means "fixes for parts involved in the event"; learning means
"developing a richer understanding of the event."

## 3. Retrospectives

Agile Manifesto, principle twelve, verbatim: **"At regular intervals, the team reflects on how to become more
effective, then tunes and adjusts its behavior accordingly."** Advocacy, not evidence — no study is cited.

No study measuring "retrospectives" as a ceremony against behaviour change was found. DORA's adjacent capabilities
are about *culture*: "Generative Organizational Culture" quotes Westrum's traits — "High cooperation," "Messengers
are trained," "Risks are shared," "Bridging is encouraged," "Failure leads to inquiry," "Novelty is implemented" —
and states "a high-trust, generative culture predicts software delivery and organizational performance." "Learning
Culture" states "an organizational culture that values learning contributes to software delivery performance,"
citing by analogy work in accounting, not DORA's own experiment. Neither page states DORA's own sample size; the
report PDFs were not opened, so **DORA's N and method are unverified here**.

## 4. The action-item problem specifically

Every source names this qualitatively, not with a completion rate. Etsy: "the pressure to fix outweighs the
pressure to learn." Adaptive Capacity Labs: orphaned action items are the default failure; progress is items
"adopted by being reviewed and cross-referenced to incidents." **No source reports a measured completion rate** —
"we wrote it down and nobody read it" is an observed pattern from practitioners, not a statistic.

## 5. Where the lesson should live

The one source with a measured effect size is Gawande's checklist study. Abstract, verbatim (Europe PMC mirror of
the NEJM record): "Between October 2007 and September 2008, eight hospitals in eight cities… participated in the
World Health Organization's Safe Surgery Saves Lives program. We prospectively collected data… from 3733
consecutively enrolled patients… We subsequently collected data on 3955 consecutively enrolled patients after the
introduction of the Surgical Safety Checklist." Result: **"The rate of death was 1.5% before the checklist was
introduced and declined to 0.8% afterward (P=0.003). Inpatient complications occurred in 11.0% of patients at
baseline and in 7.0% after introduction of the checklist (P<0.001)."** The mechanism is a 19-item checklist read
aloud *during* the task.

Toyota describes the same structural move (description, not a study): **"Jidoka in the TPS is 'automation with a
human touch'… when an abnormality occurs… the machine or equipment can detect the abnormality and stop
automatically, or the operator can stop the line by pulling the stop cord themselves… preventing them from
recurring."** Stopping happens at the moment of the defect. US Army AAR doctrine could not be reached at a
first-party URL this session, so nothing here is attributed to it. What distinguishes the two working examples: the
lesson is embedded in something that runs *at the moment the task is performed*, not in a document read only in
retrospect.

## 6. Organisational learning theory, briefly

Argyris's 1977 HBR article on double-loop learning is paywalled — title and byline confirmed, body not read. Nonaka's
SECI paper could not be reached at any mirror tried. **Both are named here only as frameworks that exist and are
widely cited — content unverified, and no evidence was found this session that either has been empirically tested.**

## 7. The AI-agent angle

Anthropic's engineering blog, "Writing tools for agents": **"Observe where your agents get stumped or confused. Read
through your evaluation agents' reasoning and feedback (or CoT) to identify rough edges,"** and **"Even small
refinements to tool descriptions can yield dramatic improvements."** On agents improving their own tools: **"You can
even let agents analyze your results and improve your tools for you. Simply concatenate the transcripts from your
evaluation agents and paste them into Claude Code."** — a human runs this; the article does not describe an agent
rewriting its own instructions unattended. These claims carry numbers in the same post (SWE-bench deltas,
token-count comparisons), not bare assertion.

The Claude Code skills docs state when a lesson should become a skill: **"Create a skill when you keep pasting the
same instructions, checklist, or multi-step procedure into chat, or when a section of CLAUDE.md has grown into a
procedure rather than a fact."** The only place the docs address self-modification versus asking a human is narrow:
`disable-model-invocation` "Set to `true` to prevent Claude from automatically loading this skill. Use for workflows
you want to trigger manually" — a control on *invocation*, not a stated policy on when to self-edit versus ask.

**No published study was found, at Anthropic or elsewhere, measuring whether an agent's self-updating instructions
improve outcomes as a general mechanism.** The numbers above measure a human editing a tool spec after reading agent
transcripts — distinct from "the agent edits its own instructions unattended." Asserting the latter improves
outcomes would be inventing a finding not in this source set.

## What I could not verify

- Argyris's 1977 HBR article body and Nonaka's SECI paper — named only, not quoted.
- US Army After Action Review doctrine — no first-party text reached.
- DORA's own sample size, years and method — capability pages state findings, not the underlying N; report PDFs not opened.
- Any quantitative postmortem action-item completion rate — every source is qualitative — and any study measuring retrospectives specifically, as opposed to organisational culture generally, or agent self-updating instructions against outcomes.

## Recommendation, for a solo developer with agents, small repositories

What the evidence supports as a **mechanism**: put the lesson where it is read *while the task is being done*, not
where it is read only if someone goes looking. That is the one pattern here with a measured effect (Gawande: death
rate 1.5%→0.8%, complications 11.0%→7.0%) and the one pattern Toyota describes structurally (stop at the defect, not
at a later review). The incident-review literature converges on the same diagnosis from the failure side: "the
pressure to fix outweighs the pressure to learn," "orphan action items" — a lesson filed somewhere that isn't the
next place the same task runs.

**"Write the lesson into the skill in the same pull request" is defensible on that basis** — the smallest version of
"the mechanism runs where the task runs": the fix's PR edits the `SKILL.md`/`CLAUDE.md`/test/CI gate that governs the
mistake, so the next invocation reads it regardless of memory. This is not itself measured — no source ran a
controlled trial of commit-adjacent documentation edits — but it is the same structural move as the one intervention
that is measured, and matches start-repo's own doctrine that a rule needs a mechanism that can fail. Where the
mistake is checkable in code, prefer a test or script over prose: prose can be skipped under the same pressure Etsy
describes crowding out learning; a failing test cannot.

**A periodic retrospective is not well supported at this scale**, and claiming otherwise would be inventing a
practice. Every source on retrospective-adjacent culture (DORA) is a cross-team survey; the mechanism it measures —
shared information flow between people — has no referent at n=1. The three failures in the prompt (a destroyed
backup ref, a silently truncated commit range, a test passing for the wrong reason) are each a single, sharp,
reproducible mistake of the kind a checklist or test catches at the point of use, not the kind a group discussion
surfaces. A retrospective ritual on top of the same-PR rule would be process for its own sake — no source here
claims it does anything the same-PR skill edit does not already do.
