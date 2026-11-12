# Dutch Lemmatization Audit

**A Dutch word-list cleaning pipeline, plus an independent audit that catches silent data errors: two different words merged together without warning.**

- **Result:** the audit checked 12,145 word groups, flagged 1,163 suspicious merges, and caught a rule that was silently merging singular nouns into plurals (e.g. *kaart* "map" into *kaarten* "maps").
- **Why it matters:** the fix chooses a *visible* mistake (an extra duplicate group) over a *silent* one (two words wrongly counted as one), because silent errors corrupt everything downstream.
- **Stack:** Python · NLP · data cleaning · data-quality auditing · simplemma

This is the cleaning, failure-analysis, audit, and correction stage of
a larger personal project ("which Dutch words should I learn first, in
what order") — isolated here because it's the part with a real
engineering decision behind it.

## What the pipeline does

**`clean_lemma_list.py`** takes a raw frequency list (token, count) and
turns it into lemma groups:

1. Lowercase every token, merge case-duplicates (`Huis`/`huis`) by
   summing their counts.
2. Drop rows with no alphabetic characters (punctuation, numbers) and
   multi-token corpus artifacts (`"1 juli"`, `"New York"` — not single
   vocabulary items).
3. Lemmatize each remaining token with [`simplemma`](https://github.com/adbar/simplemma)
   (a plain dictionary lemmatizer, Dutch), grouping surface forms under
   their lemma.

Input: `nld_mixed-typical_2012_10K-words.txt` — a word-frequency list
from the [Leipzig Corpora Collection](https://wortschatz.uni-leipzig.de/en)
(Dutch, mixed-typical, 2012, 10K-sentence corpus). **Not included in
this repo** (large, third-party licensed data) — download it from
Leipzig's site if you want to run the script yourself.

## What went wrong

`simplemma` is a plain dictionary lookup — it has no part-of-speech
information. For some words it oscillates: lemmatizing token A gives B,
and lemmatizing B gives back A (a "2-cycle"), e.g. `lopen <-> loop`,
`fiets <-> fietsen`.

An earlier version of this script resolved that oscillation by forcing
the pair onto whichever form was *longer*. That's an intuitive-looking
heuristic that happens to work for some verb pairs, but it's wrong in
general — it has no idea whether "longer" means "the correct base
form" or "a different word entirely." In practice it forced several
**singular nouns into their plurals**:

| token pair | forced onto | actual relationship |
|---|---|---|
| `kaart` / `kaarten` | `kaarten` | singular forced into plural |
| `les` / `lessen` | `lessen` | singular forced into plural |
| `tuin` / `tuinen` | `tuinen` | singular forced into plural |

and produced at least one bad three-way collision (`rijden`/`rij`/`rijen`
all merging onto `rijen`).

## How it was investigated

`audit_lemma_merges.py` is a **read-only** QA script — it never touches
the pipeline's output, it only re-derives diagnostics from the source
data and the CSV the pipeline already wrote. For every surface form it
re-runs `simplemma` in both `greedy=True` and `greedy=False` mode and
records *why* the mapping happened (`self` / `single_hop` / `two_cycle`),
then flags lemma groups against four criteria:

| flag | meaning |
|---|---|
| `multi_form` | one of the 1,000 highest-frequency lemmas that absorbed more than one surface form |
| `two_cycle` | at least one surface form sits on one side of a simplemma A↔B oscillation |
| `short_form` | a merged lemma contains a 1–2 character surface form (higher collision risk) |
| `large_merge` | a merged lemma's form-count is a statistical outlier (≥ mean + 2·stdev among merged lemmas) |

Running it against the corpus surfaced the `kaart`/`les`/`tuin`-style
plural collisions above, which is what triggered the fix.

## The fix

The "force onto the longer form" heuristic was removed. A detected
2-cycle is now left **unresolved**: each side of the oscillation keeps
itself as its own canonical lemma (`kaart → kaart`, `kaarten → kaarten`;
`lopen → lopen`, `loop → loop`). This project deliberately prefers a
**false split over a false merge**: duplicate lemma groups are visible
and recoverable, while a silent semantic conflation corrupts
downstream frequency counts.

Verified after the fix (from the actual audit output — see
[`sample_output/`](sample_output/)):

```
kaart,10,kaart|kaartje,2,multi_form|two_cycle
kaarten,7,kaarten,1,two_cycle
lessen,7,lessen,1,two_cycle
tuin,7,tuin,1,two_cycle
tuinen,1,tuinen,1,two_cycle
```

`kaart` and `kaarten` are now separate, single-form lemmas, as intended.

## Results from the actual run

Corpus: 12,145 final lemma entries.

| metric | count |
|---|---|
| Total lemma entries | 12,145 |
| Multi-form (merged) lemmas | 1,478 |
| Flagged `multi_form` (top 1,000 by frequency) | 1,000 |
| Flagged `two_cycle` | 195 |
| Flagged `short_form` | 15 |
| Flagged `large_merge` (threshold: nforms ≥ 4.04) | 53 |
| **Union of all flagged lemmas** | **1,163** |

Full breakdown in [`sample_output/lemma_merge_audit_notes.txt`](sample_output/lemma_merge_audit_notes.txt);
a representative slice of the flagged rows (highest-frequency merges,
plus every `two_cycle`/plural-collision case discussed above) is in
[`sample_output/lemma_merge_audit_sample.csv`](sample_output/lemma_merge_audit_sample.csv).

## Validation scope and known limitations

**Heuristic canonicalization is not the same thing as authoritative
semantic or part-of-speech validation.** `simplemma` is a dictionary
lookup with no grammatical awareness. The audit script's four criteria
are good at flagging *statistically suspicious* merges for a human to
check — they do not *prove* a merge is correct or incorrect. Two
concrete consequences:

- **Passing all four checks doesn't mean a lemma group is right.**
  Single-hop (non-cycle) `simplemma` mappings can still silently fold
  unrelated words together in ways that are too rare or subtle to hit
  the audit's statistical thresholds. Confirmed examples still present
  in the current output: `he → hebben`, `bijen → bij`,
  `gebruiken`/`gebruikt` → `gebruik`, `rijden → rijen`. These are
  documented, not hidden — and deliberately left unfixed for now (a
  separate future pass), tracked in the audit notes.
- **The audit is a triage tool, not a grader.** It tells you where to
  look, not what the answer is. Flagged rows still require manual
  linguistic review.

If you need certainty about lemma correctness (e.g. for anything more
than "which words should I study first"), this pipeline's output is a
starting point, not a validated lexicon — cross-check against an
authoritative POS-tagged source before relying on it.

## Requirements

```
pip install simplemma
```

Both scripts assume the corpus file and intermediate CSV sit in the
same working directory (see the `SRC`/`CSV_IN`/`OUT` constants at the
top of each file).
