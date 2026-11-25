"""Keyword (BM25) search over the store's policy documents. No API keys, no network.

Usage: python scripts/search_policies.py "how long do refunds take" [--k 3] [--dir POLICY_DIR]
Default POLICY_DIR: the skill's bundled policies/ folder (or $SHOPDESK_POLICIES).
Reads every .md and .txt file in the folder and splits it at headings ('#') and FAQ questions ('**...**').
"""

import argparse
import math
import re
from collections import Counter
from pathlib import Path

from _common import DEFAULT_POLICIES, fail, out

STOP = set("a an and are as at be by can do does for from how i if in is it my of on or our the this to "
           "what when where which who why will with you your".split())


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def sections(path):
    title, buf, found = path.stem, [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#{1,6}\s+(.*)", line) or re.match(r"^\*\*(.+)\*\*\s*$", line)
        if heading:
            if "".join(buf).strip():
                found.append((title, "\n".join(buf).strip()))
            title, buf = heading.group(1).strip(), [line]
        else:
            buf.append(line)
    if "".join(buf).strip():
        found.append((title, "\n".join(buf).strip()))
    return [(path.name, t, b) for t, b in found]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--dir", default=str(DEFAULT_POLICIES))
    a = ap.parse_args()

    folder = Path(a.dir).expanduser()
    files = sorted(list(folder.glob("*.md")) + list(folder.glob("*.txt")))
    if not files:
        fail(f"No .md or .txt policy files found in {folder}.")
    docs = [s for f in files for s in sections(f)]
    toks = [tokens(t + " " + b) for _, t, b in docs]
    n, avg = len(docs), sum(map(len, toks)) / max(len(docs), 1)
    df = Counter(w for d in toks for w in set(d))
    q = tokens(a.query)

    def bm25(d, k1=1.5, b=0.75):
        tf = Counter(d)
        return sum(math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * tf[w] * (k1 + 1)
                   / (tf[w] + k1 * (1 - b + b * len(d) / avg)) for w in q if w in tf)

    ranked = sorted(((bm25(d), i) for i, d in enumerate(toks)), reverse=True)
    hits = [{"source": docs[i][0], "section": docs[i][1], "score": round(s, 3), "text": docs[i][2]}
            for s, i in ranked[: a.k] if s > 0]
    out({"ok": True, "query": a.query, "results": hits} if hits else
        {"ok": True, "query": a.query, "results": [], "note": "No matching policy text. Do not guess; say so."})


if __name__ == "__main__":
    main()
