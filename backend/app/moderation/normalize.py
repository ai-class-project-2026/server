"""Step ① of plan 9.2. TODO(part 3): digit/symbol → hangul candidates, jamo
recombination ("ㅅㅣ발" → "시발"), keyboard mapping ("tlqkf" → "시발"), repeat squeeze.

Contract: return (normalized, index_map) where index_map[i] is the index in the
original text of normalized[i]. moderate() uses it to mask the right characters.
"""

import unicodedata


def normalize(text: str) -> tuple[str, list[int]]:
    out, index_map = [], []
    for i, ch in enumerate(text):
        # NFKC folds fullwidth "ｓｉｂａｌ" etc., but would turn compatibility jamo
        # ("ㅅㅂ") into conjoining jamo, so leave those alone.
        folded = ch if "\u3130" <= ch <= "\u318f" else unicodedata.normalize("NFKC", ch)
        for c in folded.lower():
            if (
                c.isalnum()
            ):  # drops spaces and symbols: "씨@발", "시 발" → "씨발", "시발"
                out.append(c)
                index_map.append(i)
    return "".join(out), index_map
