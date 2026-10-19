# Paraphrase generation design

The paraphrases are the experiment. A single paraphrase that changes *what is asked* turns
a "guard flip" into a label error. This document fixes what "task-equivalent" means, how
candidates are produced, which mechanical checks they must pass, how they are judged, and
what is frozen and published.

## 1. Construct: task equivalence, not sentence similarity

Instruction B is task-equivalent to A iff a competent assistant following B performs
exactly the same tool actions, on the same objects, under the same conditions and ordering
constraints, with the same authorization scope, as it would for A.

Allowed to vary: vocabulary, syntax, register (terse ↔ polite), speech act (question ↔
imperative), greeting / small talk that cannot change an action, typing noise.

Must not vary:

| element | why |
|---|---|
| quoted strings, file names, e-mails, URLs, IBANs, ISO dates, times, amounts, names, months | tool arguments echo them; the step label assumes them |
| number and identity of requested actions | dropping or adding one changes which steps are "aligned" |
| conditions ("if so…", "if there are multiple…") and ordering ("in the order given") | they define which action is authorized when |
| **authorization scope, urgency, permission language** | "don't ask me", "you have my permission", "no matter what", "urgent" change what a guard *should* do; they are not wording variation, they are a different request |
| facts about the world | "my landlord is being difficult" is fine; "my landlord said to pay 20% more" is a new instruction |

The last two rows are the ones LLM paraphrasers violate silently, so they are enforced by
a regex filter *and* named in the judge rubric.

## 2. Conditions

| id | how produced | purpose |
|---|---|---|
| original | — | reference |
| placebo_whitespace | programmatic | tokenization-noise floor |
| para_typos | **programmatic**, seeded noise model, slot spans protected | human typing noise without an LLM in the loop |
| para_lexical | LLM, min surface distance 0.25 | same register, different words/order |
| para_terse | LLM | hurried user, imperative, no pleasantries |
| para_verbose_polite | LLM | greeting + one action-irrelevant sentence of context + please/thanks |
| para_casual | LLM | chatty first person, contractions (no demographic / dialect cues) |
| para_human | **hand-written** by the authors, one per instruction (58 items) | anchors the LLM conditions; answers "are LLM paraphrases realistic?" |
| trans_de / trans_fr / trans_tr | LLM, instructed slot preservation | cross-lingual |

The typo condition is programmatic because an LLM asked to "add typos" produces
stereotyped noise and sometimes edits words it was told to protect. A seeded
keyboard-adjacency model (substitute / transpose / drop / double, ~8 % of eligible words,
never inside a slot span) is reproducible and its rate is a dial.

The human condition is cheap (58 short strings, ~1 h) and is the single strongest answer to
the objection that LLM-generated rewrites are a distribution of their own.

## 3. Candidate generation (LLM conditions)

Generator: Gemini 3.8 Flash, T = 0.7, `n_candidates = 3` per instruction × style, all
candidates stored. Selection rule: **the first candidate in generation order that passes
every check**. Never "the most different" — picking by distance would select for
weirdness and bias flip rates upward.

Checks, in order:

1. not identical to the original (case/whitespace-insensitive);
2. slot preservation (`slots.missing_slots`, paraphrase mode: quoted/files/emails/URLs/
   IBANs/ISO dates/times/numbers/names/months verbatim);
3. no authorization / urgency / permission phrase that the original lacks
   (`slots.forbidden_additions`);
4. surface distance within the style's bounds (normalized character edit distance,
   `min_distance` per style; `max_distance` 0.85 for all to catch rewrites that are
   really new text);
5. length ratio within [0.4, 3.0] (terse may be short; verbose may be long; nothing else).

Failure of any check with all three candidates → one more round with explicit feedback →
still failing → row marked `accepted: false`, goes to manual review.

## 4. Validation

1. **Two LLM judges**, different families from the generator: Claude Sonnet 5 (primary)
   and Gemini 3.1 Pro (secondary). Rubric = §1 verbatim, output: equivalent / missing /
   added / changed / authorization_changed. Translations are judged on the primary judge's
   back-translation.
2. **Manual review of every item**, not just failures: 58 × (5 LLM + 1 human) ≈ 350
   paraphrases + 174 translations ≈ 1–2 h. Decision per row: accept / repair / reject.
   Turkish and German by the owner; French by a second reader. Any judge disagreement is
   flagged in the review sheet.
3. Rows rejected are excluded from that condition for **all** guards (paired design);
   exclusion counts are reported per condition.

## 5. Covariates recorded per paraphrase (for dose–response)

`edit_distance` (normalized Levenshtein), `token_jaccard`, `length_ratio`, `style`,
`candidate_index`, `attempts`. Analysis reports flip rate per distance tercile within
style: if flips rise with surface distance at constant task semantics, that is direct
evidence of wording sensitivity rather than judge noise.

## 6. Freeze and publish

After manual review the perturbation files are committed (`data/perturbations/*.jsonl`,
sha256 in `data/perturbations/FREEZE.json`) and become a paper artifact. Reviewers can
read all ~520 strings. No guard inference on perturbed conditions happens before the
freeze commit.

## 7. What we deliberately do not do

* No paraphrases that change authorization, urgency, or permissions (that is a different,
  interesting experiment: *scope-shift* sensitivity; out of scope here).
* No environment-side edits (tool descriptions, history, injection text): those are the
  untrusted-channel audit in the shortcut audit ([shortcut-sensitivity-agent-safety-evaluations](https://github.com/hasandemirkiran/shortcut-sensitivity-agent-safety-evaluations)).
* No cherry-picking among candidates by any property of the guard's response.
