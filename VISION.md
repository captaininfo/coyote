# Coyote — Vision & the Measurement Ladder

This is the **technical/research** vision, written for people deciding whether to build with us. The narrative version — the "why should I care" story — lives in the README. This document is the map of what Coyote is trying to *become*, which rungs are already real, and which are well-scoped work a community can take on. We label the difference honestly throughout, because the whole project is an argument that you shouldn't have to trust anyone's opinion of itself — including ours.

## The premise: model the mind as a black box

Coyote doesn't claim to see inside your head. It records two observable corpora on your own machine and studies the relationship between them:

- **Inputs** (`content_role: "input"`) — what you consumed (Webpages today; eventually anything you read/watch/visit).
- **Outputs** (`content_role: "output"`) — what you produced (Annotations today; eventually your notes, prompts, goals, documents).

Keeping these two corpora **distinct** is foundational, not cosmetic — it's what makes questions like "how does what I wrote relate to what I read?" even *expressible*. A single undifferentiated pile can't answer them.

## The measurement ladder

Each rung is built on the one below it. This is the order Coyote climbs.

**Rung 0 — The record, and its shape. `built.`**
Every input is resolved to real-world concepts (WikiData) and placed in a hierarchy, so your reading has a visible *shape* — a coverage map with gaps, not a word cloud. See `demos/knowledge_shape.py`. This is the hero: an honest, inspectable picture of where your attention has actually gone.

**Rung 1 — Source inference. `built — as the positive control, not the headline.`**
Given a piece of your writing, cosine-rank your reading to surface the sources that likely informed it. On one real user's data (n=83 substantive notes, one extended session) the true source landed at a **median rank of 2**, was the single best match **37%** of the time, and fell in the **top five 67%** of the time. This is deliberately the *smallest* claim — a checkable control proving the record measures something real (there's a ground-truth `HAS_ANNOTATION` edge to check against). See `demos/source_inference.py`. State the caveats plainly: one user, one session, clustered sources; it demonstrates above-chance relatedness, not "finds hidden connections."

**Rung 2 — Divergence. `infrastructure built; the explanation is not.`**
The same geometry that finds your sources can quantify *how far* your take pulls away from them (`1 − cos(output, source)`). The vectors and provenance edges exist today; what doesn't yet is the step that turns a divergence number into an *explanation* of the difference (where you extended, pushed back, or went somewhere the source didn't). A **connection** — "this reminds me of X" — is a sibling idea: a directional edge *between nodes*, where divergence is a scalar *over vectors*.

**Rung 3 — Longitudinal conceptual change. `vision.`**
Because the record is continuous and its embeddings are pinned and dated, comparing output-over-time against the inputs that arrived in between could show how your understanding of a topic actually *moved*. The pieces (dated input/output embeddings, one coherent record) exist; the trustworthy analysis over them does not, yet. This is the rung the careful architecture below is *for*.

## The flagship community rung: flat → DAG ontology

Corpus-level divergence — comparing your writing against the *concept-filtered* input corpus at a chosen level of abstraction — needs the ontology to be a real graph. Today concepts are attached to pages *flat*, and concepts aren't linked to each other, so "the ancestors of concept X" isn't answerable by traversal. Materializing `concept → parent` edges (WikiData P279/P31) turns the hierarchy into a traversable DAG and unlocks the stratification that Rung 2's corpus case depends on. The `knowledge_shape` demo already reconstructs this arrangement *in memory* — this contribution makes it real edges. It's the single highest-leverage community rung, and a strong first issue (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Design rules that protect the vision

If you build here, these are the invariants that keep the higher rungs reachable:

- **LLMs verbalize; they do not compute the record.** No stochastic step in the data pipeline — reproducibility is the whole point of an idiographic instrument.
- **One coherent unit of content per embedding.** Cross-node relationships are edges, recombined by traversal — never concatenated into a vector. This keeps the units losslessly recombinable for analyses we haven't built yet.
- **Preserve role labels** (`input`/`output`) when assembling context — don't merge the two corpora into an unlabeled blob; downstream reasoning must distinguish consumed from produced.
- **Pin the longitudinal invariants** — embedding model, exact embedded text, timestamps. They're the precondition for every rung above 0.

## Honest framing

Some of this is real and measured today, on your own machine. Some of it is a well-scoped, doable amount of work — the kind a community can build together — and it's labeled as such above, deliberately, so it inspires collaborators rather than reading as one developer's overreach. None of it asks you to trust a model's opinion of itself. **Coyote furnishes the evidence. You infer the meaning.**
