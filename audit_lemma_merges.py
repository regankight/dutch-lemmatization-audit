#!/usr/bin/env python3
"""
QA audit of the lemma merges produced by clean_lemma_list.py.

This script does NOT touch the pipeline or its output CSV. It only reads:
  - nld_dutch_lemma_frequency.csv          (current pipeline output containing the lemma groupings being audited)
  - nld_mixed-typical_2012_10K-words.txt   (source, to rerun diagnostics on individual forms)

and writes two new files:
  - lemma_merge_audit.csv         flagged rows for manual QA
  - lemma_merge_audit_notes.txt   summary counts + specific-case diagnostics

Flags (a lemma can carry more than one):
  multi_form   top 1000 lemmas (by total_frequency) among all lemmas that merged >1 surface form
  two_cycle    lemma has >=1 surface form that is one side of a simplemma A<->B oscillation
               (e.g. lopen<->loop, fiets<->fietsen). As of the 2-cycle fix in clean_lemma_list.py,
               such a form is left as its OWN lemma rather than merged - so this flag no longer
               marks a bad merge by itself, it marks "this lemma sits on one side of a former
               oscillating pair" (worth a look, but the merge risk it used to carry is gone).
               A lemma can still be multi-form AND two_cycle at once if OTHER, non-cycling forms
               single-hop map onto it (e.g. lopen picks up loopt/liep/lopende normally, while
               lopen itself is flagged two_cycle because lopen<->loop oscillates).
  short_form   merged lemma (>1 form) that contains a surface form of length 1-2 characters
  large_merge  merged lemma whose form count is a statistical outlier among merged lemmas
               (nforms >= mean + 2*stdev, computed over all multi-form lemmas; threshold printed
               in the notes file)

For every flagged lemma we also record, per surface form, HOW its mapping arose:
  self        - simplemma left the token unchanged, OR the token was one side of a 2-cycle and
                was deliberately left as its own lemma (see two_cycle path below)
  single_hop  - plain simplemma.lemmatize(token) -> a different token, no oscillation detected
  two_cycle   - simplemma oscillates between token and its lemmatize() result; the token is kept
                as its own canonical lemma (false split preferred over false merge)

This lets us separate the possible causes of any given single-hop merge:
  1. plain simplemma dictionary lookup (single_hop, same result greedy or not)
  2. simplemma's greedy=True fuzzy matching specifically (single_hop, differs from greedy=False)
Cycle cases (two_cycle) are no longer a merge cause - they're reported so you can confirm the
fix actually split them apart in the CSV.
"""
import csv
import re
import statistics
from collections import defaultdict

import simplemma

SRC = "nld_mixed-typical_2012_10K-words.txt"
CSV_IN = "nld_dutch_lemma_frequency.csv"
AUDIT_CSV = "lemma_merge_audit.csv"
NOTES_TXT = "lemma_merge_audit_notes.txt"
LANG = "nl"
WHITESPACE_RE = re.compile(r"\s")


def has_alpha(t):
    return any(c.isalpha() for c in t)


def load_filtered_counts():
    """Reproduce stage 1+2 of the pipeline (read-only, just for reference counts)."""
    counts = defaultdict(int)
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 3:
                continue
            _id, tok, cnt = parts
            try:
                cnt = int(cnt)
            except ValueError:
                continue
            counts[tok.lower()] += cnt
    return {t: c for t, c in counts.items() if not WHITESPACE_RE.search(t) and has_alpha(t)}


_diag_cache = {}


def lemmatize_diag(token, greedy=True):
    """Mirrors clean_lemma_list.canonical_lemma() but also returns provenance.
    As of the 2-cycle fix, a detected oscillation (A<->B) is left UNRESOLVED:
    the token stays its own canonical lemma rather than being forced onto the
    other side of the cycle. This must match clean_lemma_list.py exactly, or
    this audit would diagnose a pipeline that no longer exists."""
    key = (token, greedy)
    if key in _diag_cache:
        return _diag_cache[key]
    l1 = simplemma.lemmatize(token, lang=LANG, greedy=greedy)
    if l1 == token:
        result = (token, "self")
    else:
        l2 = simplemma.lemmatize(l1, lang=LANG, greedy=greedy)
        if l2 == token:
            result = (token, "two_cycle")  # left unresolved: token is its own lemma
        else:
            result = (l1, "single_hop")
    _diag_cache[key] = result
    return result


def cause_for_form(form, current_lemma):
    """Classify why `form` ended up mapped to `current_lemma` in the pipeline (greedy=True)."""
    g_res, g_path = lemmatize_diag(form, greedy=True)
    ng_res, ng_path = lemmatize_diag(form, greedy=False)
    if g_path == "two_cycle":
        cause = "2-cycle detected (simplemma oscillates for this token) - left unresolved, token kept as its own lemma"
    elif g_res != ng_res or g_path != ng_path:
        cause = "greedy=True changes the result vs greedy=False (fuzzy/greedy matching drove this)"
    elif g_path == "single_hop":
        cause = "plain simplemma dictionary lookup (same result with greedy on or off)"
    else:
        cause = "token was left unchanged by simplemma (self)"
    return {
        "greedy_result": g_res, "greedy_path": g_path,
        "nongreedy_result": ng_res, "nongreedy_path": ng_path,
        "cause": cause,
    }


def main():
    with open(CSV_IN, encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))

    lemma_info = []
    for row in csv_rows:
        forms = row["forms"].split("|") if row["forms"] else []
        lemma_info.append({
            "lemma": row["lemma"],
            "total": int(row["total_frequency"]),
            "forms": forms,
            "nforms": len(forms),
        })

    multi = [r for r in lemma_info if r["nforms"] > 1]

    # --- provenance per (lemma, form) using the same greedy=True path as the pipeline ---
    provenance = {}
    for r in lemma_info:
        for form in r["forms"]:
            _, path = lemmatize_diag(form, greedy=True)
            provenance[(r["lemma"], form)] = path

    # Criterion 1: top 1000 multi-form lemmas by total_frequency
    top1000_multi = sorted(multi, key=lambda r: -r["total"])[:1000]
    top1000_lemmas = {r["lemma"] for r in top1000_multi}

    # Criterion 2: two_cycle - lemma has >=1 form whose provenance path is two_cycle
    two_cycle_lemmas = {lemma for (lemma, form), path in provenance.items() if path == "two_cycle"}

    # Criterion 3: short_form - multi-form lemma containing a 1-2 char surface form
    short_form_lemmas = {r["lemma"] for r in multi if any(len(f) <= 2 for f in r["forms"])}

    # Criterion 4: large_merge - statistical outlier in nforms among multi-form lemmas
    nforms_list = [r["nforms"] for r in multi]
    mean_n = statistics.mean(nforms_list)
    std_n = statistics.pstdev(nforms_list)
    threshold = mean_n + 2 * std_n
    large_merge_lemmas = {r["lemma"] for r in multi if r["nforms"] >= threshold}

    flagged = defaultdict(set)
    for lemma in top1000_lemmas:
        flagged[lemma].add("multi_form")
    for lemma in two_cycle_lemmas:
        flagged[lemma].add("two_cycle")
    for lemma in short_form_lemmas:
        flagged[lemma].add("short_form")
    for lemma in large_merge_lemmas:
        flagged[lemma].add("large_merge")

    lemma_lookup = {r["lemma"]: r for r in lemma_info}

    audit_rows = []
    for lemma, flags in flagged.items():
        r = lemma_lookup[lemma]
        audit_rows.append({
            "lemma": lemma,
            "total_frequency": r["total"],
            "forms": "|".join(r["forms"]),
            "number_of_forms": r["nforms"],
            "reason_flag": "|".join(sorted(flags)),
        })
    audit_rows.sort(key=lambda r: -r["total_frequency"])

    with open(AUDIT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["lemma", "total_frequency", "forms", "number_of_forms", "reason_flag"]
        )
        w.writeheader()
        for row in audit_rows:
            w.writerow(row)

    # ---- notes: summary + specific-case diagnostics ----
    notes = []
    notes.append("QA AUDIT NOTES - lemma merge diagnostics")
    notes.append("=" * 60)
    notes.append(f"Total lemma entries in CSV: {len(lemma_info)}")
    notes.append(f"Total multi-form (merged) lemmas: {len(multi)}")
    notes.append(f"Criterion 1 multi_form  (top 1000 merged lemmas by total_frequency): {len(top1000_lemmas)}")
    notes.append(f"Criterion 2 two_cycle   (>=1 form mapped via 2-cycle collapse):       {len(two_cycle_lemmas)}")
    notes.append(f"Criterion 3 short_form  (merged lemma contains a 1-2 char form):      {len(short_form_lemmas)}")
    notes.append(
        f"Criterion 4 large_merge (nforms >= mean+2*stdev = {threshold:.2f}, "
        f"mean={mean_n:.2f}, stdev={std_n:.2f}): {len(large_merge_lemmas)}"
    )
    notes.append(f"Union of all flagged lemmas written to {AUDIT_CSV}: {len(flagged)}")

    notes.append("\n" + "=" * 60)
    notes.append("SPECIFIC CASE DIAGNOSTICS")
    notes.append("=" * 60)

    def report_case(title, forms, current_lemma_hint=None):
        notes.append(f"\n--- {title} ---")
        for form in forms:
            diag = cause_for_form(form, current_lemma_hint)
            notes.append(f"  form: '{form}'")
            notes.append(f"    greedy=True  -> '{diag['greedy_result']}' (path: {diag['greedy_path']})")
            notes.append(f"    greedy=False -> '{diag['nongreedy_result']}' (path: {diag['nongreedy_path']})")
            notes.append(f"    cause: {diag['cause']}")
        # show which CSV lemma group these forms actually landed in
        seen_lemmas = set()
        for r in lemma_info:
            if any(f in r["forms"] for f in forms):
                seen_lemmas.add((r["lemma"], tuple(r["forms"])))
        for lemma, group_forms in seen_lemmas:
            notes.append(f"  -> current CSV group: lemma='{lemma}' forms={list(group_forms)}")

    report_case("he -> hebben", ["he"])
    report_case("fiets / fietsen / gefietst (post-fix: fiets<->fietsen no longer merged)",
                ["fiets", "fietsen", "gefietst"])
    report_case("bij / bijen -> ?", ["bij", "bijen"])

    with open(NOTES_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(notes))

    print("\n".join(notes))
    print(f"\nWrote {AUDIT_CSV} ({len(audit_rows)} rows) and {NOTES_TXT}")


if __name__ == "__main__":
    main()
