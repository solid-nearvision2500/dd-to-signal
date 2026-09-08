"""Matching paragraphs across two filings.

A year-on-year 10-K diff is not a line diff. The paragraphs move: a company
reorders its risk factors, splits one in two, promotes a sub-point to its own
heading. ``difflib`` on the paragraph list handles none of that -- move a
paragraph from position 4 to position 40 and you get one deletion and one
addition, which reads as "they deleted a risk and added an unrelated one" when
in fact nothing changed but the running order.

So this is a matching problem, solved in three passes, cheapest first:

1. **Exact.** Normalise and hash. In a typical 10-K, 80-90% of paragraphs come
   through untouched, and this pass removes them for the price of a dict.
2. **Near.** TF-IDF over word bigrams, cosine similarity, greedy best-first
   pairing above a threshold. This catches the edited paragraphs, which are the
   ones anybody actually wants to read.
3. **Leftovers.** Whatever failed to pair is a genuine addition or deletion.

The greedy step is not the optimal assignment -- that would be the Hungarian
algorithm -- but on real filings the similarity matrix is close to a permutation
matrix, so greedy and optimal agree almost everywhere, and greedy does not need
a quadratic-memory solver on a 2,000-paragraph section.
"""

from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from ..extract.html import Block
from .changes import Change, Kind, normalise, similarity, word_diff

#: Below this cosine similarity, two paragraphs are different paragraphs rather
#: than one edited paragraph. Tuned by hand against Apple, NVIDIA, Ford and
#: Moderna year-pairs: 0.45 pairs up heavily-rewritten risk factors, 0.6 starts
#: splitting them into add/remove pairs, 0.3 starts pairing boilerplate that
#: merely shares vocabulary.
MATCH_THRESHOLD = 0.45

#: Guard against a pathological section pairing every paragraph with every other
#: one. 3,000 x 3,000 float32 is 36MB, which is fine; ten times that is not.
MAX_CANDIDATES = 3000


def align(old: list[Block], new: list[Block], *, section: str = "") -> list[Change]:
    """Pair up the paragraphs of two versions of a section.

    Returns changes in the reading order of the new filing, with removals slotted
    in where the deleted text used to sit relative to its neighbours.
    """
    old_texts = [b.text for b in old]
    new_texts = [b.text for b in new]

    old_free, new_free, unchanged = _exact_pass(old_texts, new_texts)
    pairs = _near_pass(old_texts, new_texts, old_free, new_free, section=section)

    matched_old = {i for i, _ in pairs}
    matched_new = {j for _, j in pairs}
    pair_by_new = dict((j, i) for i, j in pairs)

    changes: list[Change] = []
    # Walk the new filing in order. Anything removed is emitted just before the
    # next surviving paragraph, which is where a reader would expect to find it.
    removed_queue = sorted(i for i in old_free if i not in matched_old)
    removed_cursor = 0

    for j, text in enumerate(new_texts):
        old_index = pair_by_new.get(j, unchanged.get(j))

        while removed_cursor < len(removed_queue) and (
            old_index is not None and removed_queue[removed_cursor] < old_index
        ):
            i = removed_queue[removed_cursor]
            changes.append(Change(Kind.REMOVED, old=old_texts[i], section=section))
            removed_cursor += 1

        if j in unchanged:
            changes.append(
                Change(
                    Kind.UNCHANGED,
                    old=old_texts[unchanged[j]],
                    new=text,
                    similarity=1.0,
                    section=section,
                )
            )
        elif j in matched_new:
            i = pair_by_new[j]
            changes.append(
                Change(
                    Kind.MODIFIED,
                    old=old_texts[i],
                    new=text,
                    similarity=similarity(old_texts[i], text),
                    spans=word_diff(old_texts[i], text),
                    section=section,
                )
            )
        else:
            changes.append(Change(Kind.ADDED, new=text, section=section))

    for i in removed_queue[removed_cursor:]:
        changes.append(Change(Kind.REMOVED, old=old_texts[i], section=section))

    return changes


def _exact_pass(old: list[str], new: list[str]) -> tuple[list[int], list[int], dict[int, int]]:
    """Pair identical paragraphs. Returns the unpaired indices, and new->old."""
    buckets: dict[str, list[int]] = {}
    for i, text in enumerate(old):
        buckets.setdefault(normalise(text), []).append(i)

    unchanged: dict[int, int] = {}
    new_free: list[int] = []
    for j, text in enumerate(new):
        key = normalise(text)
        candidates = buckets.get(key)
        if candidates:
            # Pop rather than reuse: a boilerplate sentence repeated three times
            # in the old filing and twice in the new one should register as one
            # deletion, not as three matches.
            unchanged[j] = candidates.pop(0)
        else:
            new_free.append(j)

    taken = set(unchanged.values())
    old_free = [i for i in range(len(old)) if i not in taken]
    return old_free, new_free, unchanged


def _near_pass(
    old: list[str],
    new: list[str],
    old_free: list[int],
    new_free: list[int],
    *,
    section: str = "",
) -> list[tuple[int, int]]:
    """Greedily pair the leftovers by cosine similarity."""
    if not old_free or not new_free:
        return []
    if len(old_free) > MAX_CANDIDATES or len(new_free) > MAX_CANDIDATES:
        old_free = old_free[:MAX_CANDIDATES]
        new_free = new_free[:MAX_CANDIDATES]

    corpus = [old[i] for i in old_free] + [new[j] for j in new_free]
    try:
        # Bigrams, not unigrams: filings share so much vocabulary that unigram
        # cosine similarity puts two unrelated risk factors at 0.5.
        vectoriser = TfidfVectorizer(
            ngram_range=(1, 2), min_df=1, sublinear_tf=True, strip_accents="unicode"
        )
        matrix = vectoriser.fit_transform(corpus)
    except ValueError:
        # Every document was stop words or empty. Nothing to pair.
        return []

    split = len(old_free)
    sims = (matrix[:split] @ matrix[split:].T).toarray()

    pairs: list[tuple[int, int]] = []
    used_rows: set[int] = set()
    used_cols: set[int] = set()

    # Best-first over every candidate above the threshold. argsort on the
    # flattened matrix is the whole algorithm.
    flat = sims.ravel()
    order = np.argsort(flat)[::-1]
    width = sims.shape[1]
    for index in order:
        score = flat[index]
        if score < MATCH_THRESHOLD:
            break
        row, col = divmod(int(index), width)
        if row in used_rows or col in used_cols:
            continue
        used_rows.add(row)
        used_cols.add(col)
        pairs.append((old_free[row], new_free[col]))

    return pairs
