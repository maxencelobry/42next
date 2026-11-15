"""push_swap helpers: exact stack simulator, input generators, disorder.

Stdlib only. Index 0 of a stack is its top (the first argument).
"""

from collections import Counter, deque
from fractions import Fraction

OPS = ("sa", "sb", "ss", "pa", "pb", "ra", "rb", "rr", "rra", "rrb", "rrr")
OPS_SET = set(OPS)


def _swap(s):
    if len(s) >= 2:
        s[0], s[1] = s[1], s[0]


def _rot(s):
    if len(s) >= 2:
        s.rotate(-1)


def _rrot(s):
    if len(s) >= 2:
        s.rotate(1)


def apply(op, a, b):
    if op == "sa":
        _swap(a)
    elif op == "sb":
        _swap(b)
    elif op == "ss":
        _swap(a)
        _swap(b)
    elif op == "pa":
        if b:
            a.appendleft(b.popleft())
    elif op == "pb":
        if a:
            b.appendleft(a.popleft())
    elif op == "ra":
        _rot(a)
    elif op == "rb":
        _rot(b)
    elif op == "rr":
        _rot(a)
        _rot(b)
    elif op == "rra":
        _rrot(a)
    elif op == "rrb":
        _rrot(b)
    elif op == "rrr":
        _rrot(a)
        _rrot(b)
    else:
        raise ValueError(op)


class Parsed:
    """stdout split into operations, with the first format problem."""

    def __init__(self, ops, bad_line=None, bad_text=None, why=None):
        self.ops = ops
        self.bad_line = bad_line      # 1-based line number
        self.bad_text = bad_text
        self.why = why

    @property
    def ok(self):
        return self.bad_line is None


def parse_ops(out):
    """Operations must be one per line, each followed by \\n, nothing else."""
    text = out.decode("utf-8", "surrogateescape")
    if text == "":
        return Parsed([])
    lines = text.split("\n")
    trailing = lines[-1]
    lines = lines[:-1]
    ops = []
    for i, line in enumerate(lines, 1):
        if line not in OPS_SET:
            why = "empty line" if line == "" else "invalid instruction"
            if line.strip() in OPS_SET:
                why = "instruction with extra whitespace"
            return Parsed(ops, i, line, why)
        ops.append(line)
    if trailing != "":
        if trailing in OPS_SET:
            return Parsed(ops, len(lines) + 1, trailing,
                          "last instruction not terminated by \\n")
        return Parsed(ops, len(lines) + 1, trailing, "invalid instruction")
    return Parsed(ops)


class Sim:
    """Replay ops on a copy of nums. Records the final state."""

    def __init__(self, nums, ops):
        self.a = deque(nums)
        self.b = deque()
        for op in ops:
            apply(op, self.a, self.b)
        self.target = sorted(nums)
        self.count = Counter(ops)

    @property
    def sorted(self):
        return not self.b and list(self.a) == self.target

    def first_unsorted(self):
        a = list(self.a)
        for i in range(1, len(a)):
            if a[i - 1] > a[i]:
                return i - 1
        return None


def show_stack(s, focus=None, width=14):
    """'[1 2 3 ... +40]' (top first), centred on focus when given."""
    s = list(s)
    if len(s) <= width:
        return "[" + " ".join(map(str, s)) + "]"
    start = 0 if focus is None else max(0, focus - width // 2)
    start = min(start, len(s) - width)
    part = s[start:start + width]
    head = f"... ({start} before) " if start else ""
    tail = len(s) - start - width
    return ("[" + head + " ".join(map(str, part))
            + (f" ... +{tail}" if tail else "") + "]")


# --------------------------------------------------------------------------
# disorder
# --------------------------------------------------------------------------

def inversions(nums):
    """Count pairs i<j with nums[i] > nums[j] (merge sort, O(n log n))."""
    def rec(arr):
        if len(arr) < 2:
            return arr, 0
        mid = len(arr) // 2
        left, x = rec(arr[:mid])
        right, y = rec(arr[mid:])
        merged, inv, i, j = [], x + y, 0, 0
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                merged.append(left[i])
                i += 1
            else:
                merged.append(right[j])
                inv += len(left) - i
                j += 1
        merged += left[i:] + right[j:]
        return merged, inv
    return rec(list(nums))[1]


def disorder(nums):
    """Exact disorder as a Fraction (0 for fewer than 2 numbers)."""
    n = len(nums)
    if n < 2:
        return Fraction(0)
    return Fraction(inversions(nums), n * (n - 1) // 2)


def with_disorder(rng, n, target, lo=-1000000, hi=1000000):
    """n distinct ints whose disorder is exactly round(target * pairs) / pairs.

    Builds a Lehmer code with the wanted number of inversions, then shuffles
    the inversions around so the input is not a trivial pattern.
    """
    pairs = n * (n - 1) // 2
    want = round(target * pairs)
    cap = [n - 1 - i for i in range(n)]
    code = [int(target * c) for c in cap]
    diff = want - sum(code)
    idx = list(range(n))
    while diff:
        i = rng.choice(idx)
        if diff > 0 and code[i] < cap[i]:
            code[i] += 1
            diff -= 1
        elif diff < 0 and code[i] > 0:
            code[i] -= 1
            diff += 1
    for _ in range(6 * n):           # move inversions between positions
        i, j = rng.randrange(n), rng.randrange(n)
        if i != j and code[i] > 0 and code[j] < cap[j]:
            code[i] -= 1
            code[j] += 1
    pool = list(range(n))
    perm = [pool.pop(c) for c in code]
    values = sorted(rng.sample(range(lo, hi), n))
    return [values[r] for r in perm]


def random_input(rng, n, lo=-2147483648, hi=2147483647):
    return rng.sample(range(lo, hi + 1), n)


def regime(d):
    """Expected adaptive class for a disorder value (subject 6.3.3)."""
    if d < Fraction(1, 5):
        return "n2"
    if d < Fraction(1, 2):
        return "nsqrtn"
    return "nlogn"


CLASS_NAMES = {"n2": "O(n²)", "nsqrtn": "O(n√n)", "nlogn": "O(n log n)"}
