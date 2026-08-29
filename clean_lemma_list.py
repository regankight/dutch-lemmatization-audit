#!/usr/bin/env python3
"""
Stage 1-3 cleaner for Leipzig Dutch word-frequency lists.

Input : nld_mixed-typical_2012_10K-words.txt  (id \t token \t count)
Output: nld_dutch_lemma_frequency.csv          (lemma,total_frequency,forms)

Stages:
  1. Lowercase tokens, merge case duplicates (sum counts).
  2. Drop rows with no alphabetic characters, and drop multi-token
     corpus artifacts (tokens containing whitespace - e.g. "1 juli",
     "New York", "Geert Wilders" - none of these are single vocabulary
     lemmas anyway).
  3. Lemmatize with simplemma (Dutch dictionary lemmatizer). Because a
     plain dictionary lemmatizer isn't POS-aware, some headwords
     oscillate between two related forms (e.g. lopen <-> loop,
     fiets <-> fietsen) instead of settling on one. A prior version of
     this script collapsed that 2-cycle onto the longer of the two
     forms; a QA audit found that heuristic unsafe (it turned singular
     nouns into their plural, e.g. kaart -> kaarten, les -> lessen,
     tuin -> tuinen, and produced bad collisions like rijden/rij/rijen
     -> rijen). This project prefers a false split over a false merge,
     so a detected 2-cycle is now left UNRESOLVED: each side of the
     cycle keeps itself as its own canonical lemma (lopen -> lopen,
     loop -> loop; fiets -> fiets, fietsen -> fietsen). All other,
     non-oscillating simplemma mappings are still applied normally
     (loopt -> lopen, liep -> lopen, gefietst -> fietsen, etc).

Known limitation (documented, not silently hidden): simplemma's plain
dictionary lookup is not POS-aware even outside the 2-cycle case, so a
small number of single-hop mappings still fold an unrelated word into a
common one (e.g. "he" -> "hebben", "bijen" -> "bij"). Those are left
unchanged for now and tracked separately in the QA audit - this pass
only removes the 2-cycle longer-form heuristic.
"""
import csv
import re
import sys
from collections import defaultdict

import simplemma

SRC = "nld_mixed-typical_2012_10K-words.txt"
OUT = "nld_dutch_lemma_frequency.csv"
LANG = "nl"

WHITESPACE_RE = re.compile(r"\s")


def has_alpha(token: str) -> bool:
    return any(ch.isalpha() for ch in token)


def load_and_normalize(path):
    """Stage 1: parse TSV, lowercase, sum duplicate counts."""
    counts = defaultdict(int)
    total_rows = 0
    bad_rows = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                bad_rows += 1
                continue
            _id, token, count_s = parts
            try:
                count = int(count_s)
            except ValueError:
                bad_rows += 1
                continue
            total_rows += 1
            counts[token.lower()] += count
    return counts, total_rows, bad_rows


def filter_non_vocab(counts):
    """Stage 2: drop punctuation/number-only and multi-token artifact rows."""
    kept = {}
    dropped_no_alpha = 0
    dropped_multitoken = 0
    for token, count in counts.items():
        if WHITESPACE_RE.search(token):
            dropped_multitoken += 1
            continue
        if not has_alpha(token):
            dropped_no_alpha += 1
            continue
        kept[token] = count
    return kept, dropped_no_alpha, dropped_multitoken


_lemma_cache = {}


def canonical_lemma(token):
    """Stage 3 helper: single-hop lemmatize; a detected 2-cycle is left
    UNRESOLVED (each side keeps itself as its own canonical lemma) rather
    than forced together. See module docstring for why."""
    if token in _lemma_cache:
        return _lemma_cache[token]
    l1 = simplemma.lemmatize(token, lang=LANG, greedy=True)
    if l1 == token:
        result = token
    else:
        l2 = simplemma.lemmatize(l1, lang=LANG, greedy=True)
        if l2 == token:
            # oscillating pair (e.g. lopen <-> loop): genuinely ambiguous
            # without POS info - prefer a false split over a false merge,
            # so leave the token as its own lemma instead of forcing it
            # onto the other side of the cycle.
            result = token
        else:
            result = l1
    _lemma_cache[token] = result
    return result


def lemmatize_groups(counts):
    groups = defaultdict(lambda: {"total": 0, "forms": defaultdict(int)})
    for token, count in counts.items():
        lemma = canonical_lemma(token)
        groups[lemma]["total"] += count
        groups[lemma]["forms"][token] += count
    return groups


def write_csv(groups, path):
    rows = []
    for lemma, data in groups.items():
        forms_sorted = sorted(
            data["forms"].items(), key=lambda kv: (-kv[1], kv[0])
        )
        forms_str = "|".join(f for f, _ in forms_sorted)
        rows.append((lemma, data["total"], forms_str, len(forms_sorted)))
    rows.sort(key=lambda r: (-r[1], r[0]))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["lemma", "total_frequency", "forms"])
        for lemma, total, forms_str, _ in rows:
            writer.writerow([lemma, total, forms_str])
    return rows


def main():
    counts, total_rows, bad_rows = load_and_normalize(SRC)
    after_case_merge = len(counts)

    kept, dropped_no_alpha, dropped_multitoken = filter_non_vocab(counts)
    after_filter = len(kept)

    groups = lemmatize_groups(kept)
    rows = write_csv(groups, OUT)

    multi_form_lemmas = [r for r in rows if r[3] > 1]

    print(f"Source rows parsed:         {total_rows}  (malformed skipped: {bad_rows})")
    print(f"Unique tokens after lowering + case-merge: {after_case_merge}")
    print(f"Dropped - no alphabetic chars:  {dropped_no_alpha}")
    print(f"Dropped - multi-token artifacts: {dropped_multitoken}")
    print(f"Tokens remaining for lemmatization: {after_filter}")
    print(f"Final lemma entries: {len(rows)}")
    print(f"Lemma entries combining >1 surface form: {len(multi_form_lemmas)}")
    print(f"Wrote: {path_abs(OUT)}")

    print("\nTop 25 by total_frequency:")
    for lemma, total, forms_str, nforms in rows[:25]:
        print(f"  {lemma:<15} {total:<6} {forms_str}")

    print("\nSample multi-form merges (spot check):")
    for lemma, total, forms_str, nforms in sorted(
        multi_form_lemmas, key=lambda r: -r[1]
    )[:15]:
        print(f"  {lemma:<15} {total:<6} {forms_str}")


def path_abs(p):
    import os

    return os.path.abspath(p)


if __name__ == "__main__":
    sys.exit(main())
