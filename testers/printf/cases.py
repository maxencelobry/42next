"""Test cases for ft_printf.

A case is one call (or a short sequence of calls) run twice: once with the
real printf of the libc (reference) and once with ft_printf. Output bytes and
return values are compared by run.py.
"""

import random

DEFAULT_SEED = 4242
LONG_MAX_LEN = 100000      # size of the g_long helper string in the C runner


class Case:
    """group: test group id. fmt: format (str). args: C expressions.

    ub:    behaviour not defined by the subject -> mismatch is only a WARN.
    code:  custom C body using `pf` (printf or ft_printf) and `wr(ref, s)`.
    fixed: (ret, bytes) expected result when the libc can't be the reference.
    direct: False if the case must not run in the leak checker.
    """

    def __init__(self, group, fmt=None, args=(), ub=False, code=None,
                 label=None, fixed=None, direct=True, title=None):
        self.group = group
        self.fmt = fmt
        self.args = list(args)
        self.ub = ub
        self.code = code
        self.fixed = fixed
        self.direct = direct
        self.title = title
        if label is None:
            label = "ft_printf(" + c_lit(fmt)
            if self.args:
                label += ", " + ", ".join(self.args)
            label += ")"
        self.label = label

    def c_body(self):
        if self.code:
            return self.code
        call = "pf(" + c_lit(self.fmt)
        if self.args:
            call += ", " + ", ".join(self.args)
        return f"return ({call}));"


def c_lit(text):
    """Python str/bytes -> C string literal."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    out = []
    for b in text:
        ch = chr(b)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= b < 127:
            out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    return '"' + "".join(out) + '"'


def c_char(code):
    names = {0: "'\\0'", 10: "'\\n'", 9: "'\\t'", 39: "'\\''", 92: "'\\\\'"}
    if code in names:
        return names[code]
    if 32 <= code < 127:
        return f"'{chr(code)}'"
    return str(code)


def long_str(n):
    """C expression for a string of n chars (pattern a..z) + its label."""
    return f"S({n})"


INTS = ["0", "1", "-1", "9", "-9", "10", "-10", "11", "-11", "15", "16", "17",
        "42", "-42", "99", "-99", "100", "-100", "101", "-101", "255", "-255",
        "1000", "-1000", "12345", "-12345", "999999999", "1000000000",
        "-999999999", "-1000000000", "2147483646", "INT_MAX", "INT_MIN",
        "INT_MIN + 1"]

UINTS = ["0", "1", "9", "10", "11", "15", "16", "17", "42", "99", "100",
         "101", "255", "256", "4095", "4096", "65535", "65536", "123456789",
         "2147483647", "2147483648U", "4294967294U", "UINT_MAX", "-1", "-42",
         "INT_MIN", "0xdeadbeef", "0xABCDEF", "0x7fffffff", "0x80000000"]

PTRS = ["NULL", "(void *)1", "(void *)15", "(void *)16", "(void *)17",
        "(void *)255", "(void *)-1", "(void *)LONG_MIN", "(void *)LONG_MAX",
        "(void *)(long)INT_MIN", "(void *)(long)INT_MAX", "(void *)ULONG_MAX",
        "(void *)-ULONG_MAX", "(void *)0xdeadbeef", "(void *)0x7fffffffffffUL",
        "(void *)0x8000000000000000UL", "&g_var", "g_heap", "g_long",
        "(char *)&g_var + 1"]

STRS = ['""', '" "', '"-"', '"a"', '"hello"', '"Hello, World!"', '"\\n"',
        '"\\ttab\\t"', '"50% off"', '"%d %s %% %c"', '"line1\\nline2"',
        '"\\001\\177"', '"h\\303\\251llo w\\303\\266rld \\342\\202\\254"',
        "NULL", "S(1)", "S(1000)", "S(10000)"]

CHARS = [ord("0"), ord("a"), ord("Z"), ord(" "), ord("~"), 10, 9, ord("%"),
         0, 1, 127, 39, 92]


def _per_value(group, conv, values):
    out = []
    for v in values:
        out.append(Case(group, "%" + conv, [v]))
        out.append(Case(group, " %" + conv + " ", [v]))
    return out


def mandatory_cases(seed):
    rng = random.Random(seed)
    cases = []

    # ---------------- %c
    g = "c"
    for code in CHARS:
        cases.append(Case(g, "%c", [c_char(code)]))
        cases.append(Case(g, " %c ", [c_char(code)]))
    cases += [
        Case(g, " %c", ["'0' - 256"]),
        Case(g, "%c ", ["'0' + 256"]),
        Case(g, "%c", ["200"]),
        Case(g, " %c %c %c ", ["'0'", "0", "'1'"]),
        Case(g, " %c %c %c ", ["' '", "' '", "' '"]),
        Case(g, " %c %c %c ", ["'1'", "'2'", "'3'"]),
        Case(g, " %c %c %c ", ["'2'", "'1'", "0"]),
        Case(g, " %c %c %c ", ["0", "'1'", "'2'"]),
        Case(g, "%c%c%c%c", ["'4'", "'2'", "'!'", "'\\n'"]),
        Case(g, "%c", ["'%'"], title="percent sign passed as a char"),
        Case(g, "%c%%%c", ["'%'", "'%'"]),
        Case(g, "char: [%c]\n", ["'x'"]),
    ]
    for _ in range(12):
        ch = rng.randint(32, 126)
        cases.append(Case(g, "[%c]", [c_char(ch)], title="random"))

    # ---------------- %s
    g = "s"
    for s in STRS:
        cases.append(Case(g, "%s", [s]))
        cases.append(Case(g, " %s ", [s]))
    cases += [
        Case(g, " %s %s ", ['""', '"-"']),
        Case(g, " %s %s ", ['" - "', '""']),
        Case(g, " %s %s %s %s %s", ['" - "', '""', '"4"', '""', "S(30)"]),
        Case(g, " %s %s %s %s %s ", ['" - "', '""', '"4"', '""', '"2 "']),
        Case(g, " NULL %s NULL ", ["NULL"]),
        Case(g, "%s%s%s", ["NULL", '""', "NULL"]),
        Case(g, "%s", ["S(100000)"]),
        Case(g, "[%s] [%s]", ['"a%sb"', '"%%"']),
    ]
    words = ["foo", "bar", "42", "", "printf", "%", "\\n", " ", "x y z"]
    for _ in range(12):
        picked = [rng.choice(words + ["NULL"]) for _ in range(3)]
        args = [w if w == "NULL" else c_lit(w).replace("\\\\n", "\\n")
                for w in picked]
        cases.append(Case(g, "<%s|%s|%s>", args, title="random"))

    # ---------------- %p
    g = "p"
    for p in PTRS:
        cases.append(Case(g, "%p", [p]))
        cases.append(Case(g, " %p ", [p]))
    cases += [
        Case(g, " %p %p ", ["(void *)LONG_MIN", "(void *)LONG_MAX"]),
        Case(g, " %p %p ", ["(void *)(long)INT_MIN", "(void *)(long)INT_MAX"]),
        Case(g, " %p %p ", ["(void *)ULONG_MAX", "(void *)-ULONG_MAX"]),
        Case(g, " %p %p ", ["NULL", "NULL"]),
        Case(g, "%p%p%p", ["NULL", "&g_var", "NULL"]),
    ]
    for _ in range(12):
        v = rng.getrandbits(rng.choice([8, 16, 32, 48, 64]))
        cases.append(Case(g, "%p", [f"(void *)0x{v:x}UL"], title="random"))

    # ---------------- %d / %i
    for conv in ("d", "i"):
        g = conv
        cases += _per_value(g, conv, INTS)
        f = "%" + conv
        cases += [
            Case(g, f"{f}{f}", ["INT_MIN", "INT_MAX"]),
            Case(g, f" {f} {f} {f} ", ["-1", "0", "1"]),
            Case(g, f"neg {f} and {f} text", ["-42", "-2147483647"]),
            Case(g, f"{f}\n", ["INT_MIN"]),
            Case(g, f"[{f}][{f}][{f}][{f}]", ["0", "-0", "+0", "000"]),
            Case(g, f"{f}", ["'A'"]),
        ]
        for _ in range(16):
            v = rng.randint(-2 ** 31, 2 ** 31 - 1)
            arg = "INT_MIN" if v == -2 ** 31 else str(v)
            cases.append(Case(g, f"<{f}>", [arg], title="random"))

    # ---------------- %u %x %X
    for conv in ("u", "x", "X"):
        g = conv
        cases += _per_value(g, conv, UINTS)
        f = "%" + conv
        cases += [
            Case(g, f"{f}{f}", ["0", "UINT_MAX"]),
            Case(g, f" {f} {f} {f} ", ["1", "2", "3"]),
            Case(g, f"{f}\n", ["UINT_MAX"]),
        ]
        for _ in range(16):
            v = rng.randint(0, 2 ** 32 - 1)
            cases.append(Case(g, f"<{f}>", [f"{v}U"], title="random"))

    # ---------------- %%
    g = "percent"
    for fmt in ["%%", " %% ", "%%%%", "%%%%%%", "%%%%%%%%%%", "100%%",
                "%% %% %%", "%%d", "%%c%%s%%p", "abc %% def %% ghi",
                "%%\n", "[%%]"]:
        cases.append(Case(g, fmt))
    cases += [
        Case(g, "%%%c", ["'x'"]),
        Case(g, "%c%%", ["'x'"]),
        Case(g, "%%%d%%", ["42"]),
        Case(g, "%%%s%%", ['"s"']),
        Case(g, "", title="empty format"),
        Case(g, "hello", title="format without conversion"),
        Case(g, "hello world\n"),
        Case(g, "a\tb\\c\"d"),
        Case(g, "x" * 500, label='ft_printf("x" * 500) (500-char format)'),
    ]
    for _ in range(6):
        n = rng.randint(1, 8)
        cases.append(Case(g, "".join(rng.choice(["%%", "ab", " ", "%%%%"])
                                     for _ in range(n)), title="random"))

    # ---------------- mix
    g = "mix"
    cases += [
        Case(g, "%%%c%%%s%%%d%%%i%%%u%%%x%%%X%%%% %%%c%%%s%%%d%%%i%%%u%%%x"
                "%%%X%%%% %%%c%%%s%%%d%%%i%%%u%%%x%%%X%%%% %c%%",
             ["'A'", '"42"', "42", "42", "42", "42", "42", "'B'", '"-42"',
              "-42", "-42", "-42", "-42", "42", "'C'", '"0"', "0", "0", "0",
              "0", "42", "0"]),
        Case(g, "%c%s%p%d%i%u%x%X%%",
             ["'z'", '"str"', "&g_var", "-1", "1", "UINT_MAX", "255", "255"]),
        Case(g, "c=%c s=%s p=%p d=%d i=%i u=%u x=%x X=%X %%\n",
             ["0", "NULL", "NULL", "INT_MIN", "INT_MAX", "0", "0", "0"]),
        Case(g, "%s is %d years old and has %u%% of %x", ['"Bob"', "42", "100",
                                                          "48879"]),
        Case(g, " %d " * 40, [str(i * (-1) ** i) for i in range(40)],
             label="ft_printf(\" %d \" x 40, 0, -1, 2, -3, ...) (40 args)"),
        Case(g, "%s" * 20, ['"ab"'] * 20,
             label='ft_printf("%s" x 20, "ab", ...)'),
        Case(g, "%c" * 30, [c_char(65 + i % 26) for i in range(30)],
             label="ft_printf(\"%c\" x 30, 'A', 'B', ...)"),
    ]
    pool = [("%c", lambda: c_char(rng.randint(32, 126))),
            ("%s", lambda: rng.choice(['"abc"', "NULL", '""', '"%"'])),
            ("%p", lambda: rng.choice(
                ["NULL", "&g_var", f"(void *)0x{rng.getrandbits(40):x}UL"])),
            ("%d", lambda: str(rng.randint(-2 ** 31 + 1, 2 ** 31 - 1))),
            ("%i", lambda: str(rng.randint(-1000, 1000))),
            ("%u", lambda: f"{rng.randint(0, 2 ** 32 - 1)}U"),
            ("%x", lambda: f"{rng.randint(0, 2 ** 32 - 1)}U"),
            ("%X", lambda: f"{rng.randint(0, 2 ** 32 - 1)}U"),
            ("%%", None), ("txt ", None), ("\n", None)]
    for _ in range(40):
        fmt, args = "", []
        for _ in range(rng.randint(2, 9)):
            spec, gen = rng.choice(pool)
            fmt += spec
            if gen:
                args.append(gen())
        cases.append(Case(g, fmt, args, title="random"))

    # ---------------- return value
    g = "return"
    cases += [
        Case(g, "", title="empty format returns 0"),
        Case(g, "%c", ["'\\0'"], title="'\\0' is written and counted"),
        Case(g, "a%cb", ["0"]),
        Case(g, "%s", ['""']),
        Case(g, "%s", ["NULL"]),
        Case(g, "%p", ["NULL"]),
        Case(g, "%d", ["INT_MIN"]),
        Case(g, "%u", ["UINT_MAX"]),
        Case(g, "%x%X", ["UINT_MAX", "UINT_MAX"]),
        Case(g, "%%"),
        Case(g, "%s", ["S(100000)"]),
        Case(g, "%s%s", ["S(50000)", "S(50000)"]),
        Case(g, None, code='int r = pf("abc"); wr(ref, "|"); '
                           'r += pf("%d", 42); wr(ref, "|"); '
                           'return (r + pf("%s", "xyz"));',
             label='ft_printf("abc") + write + ft_printf("%d", 42) + '
                   'write + ft_printf("%s", "xyz")  (sum of returns, no '
                   'buffering between calls)'),
        Case(g, None, code="int r = 0; int i = 0; while (i++ < 100) "
                           "r += pf(\"%d,\", i); return (r);",
             label='100 x ft_printf("%d,", i)  (sum of returns)'),
    ]
    return cases


def bonus_cases(seed):
    rng = random.Random(seed + 1)
    g = "bonus"
    cases = []
    widths = ["1", "2", "5", "10"]
    for code in (ord("a"), 0, ord("%")):
        for w in widths:
            cases.append(Case(g, f"[%{w}c]", [c_char(code)]))
            cases.append(Case(g, f"[%-{w}c]", [c_char(code)]))
    for s in ['""', '"abc"', '"hello world"', "S(20)"]:
        for spec in ["%5s", "%-5s", "%.0s", "%.1s", "%.3s", "%.10s", "%10.3s",
                     "%-10.3s", "%1s", "%-1s", "%.s", "%20s", "%-20s|"]:
            cases.append(Case(g, f"[{spec}]", [s]))
    for spec in ["%10s", "%-10s", "%.6s", "%10.6s"]:
        cases.append(Case(g, f"[{spec}]", ["NULL"]))
    for p in ["NULL", "&g_var", "(void *)1", "(void *)ULONG_MAX"]:
        for spec in ["%1p", "%5p", "%20p", "%-20p", "%-5p"]:
            cases.append(Case(g, f"[{spec}]", [p]))
    ints = ["0", "1", "-1", "42", "-42", "12345", "-12345", "INT_MAX",
            "INT_MIN"]
    dspecs = ["%5d", "%-5d", "%05d", "%.5d", "%.0d", "%.d", "%8.5d", "%-8.5d",
              "%08.5d", "%+d", "% d", "%+5d", "%-+6d", "% 5d", "%+05d",
              "% 05d", "%+.3d", "% .3d", "%015d", "%-15d", "%.15d", "%1d",
              "%5.0d", "%-5.0d", "%+.0d", "% .0d", "%012.10d"]
    for conv in ("d", "i"):
        for v in ints:
            for spec in dspecs:
                cases.append(Case(g, "[" + spec.replace("d", conv) + "]", [v]))
    uints = ["0", "1", "42", "255", "4096", "UINT_MAX", "-1"]
    uspecs = ["%5u", "%-5u", "%05u", "%.5u", "%.0u", "%8.5u", "%-8.5u",
              "%08.5u", "%015u", "%5.0u"]
    for v in uints:
        for spec in uspecs:
            cases.append(Case(g, f"[{spec}]", [v]))
    xspecs = ["%5x", "%-5x", "%05x", "%.5x", "%.0x", "%8.5x", "%#x", "%#5x",
              "%#-8x", "%#08x", "%#.5x", "%#10.5x", "%-#10.5x", "%#.0x",
              "%08.3x", "%#15x"]
    for v in uints:
        for spec in xspecs:
            cases.append(Case(g, f"[{spec}]", [v]))
            cases.append(Case(g, "[" + spec.replace("x", "X") + "]", [v]))
    cases += [
        Case(g, "%-3c|%-3s|%-3d|%-3x|%-3p", ["'a'", '"b"', "1", "2", "NULL"]),
        Case(g, "%5c|%5s|%5d|%5x|%5p", ["'a'", '"b"', "1", "2", "NULL"]),
        Case(g, "%+d % d %#x %#X %05d %-5d| %.3s", ["1", "2", "255", "255",
                                                    "-7", "-7", '"abcdef"']),
        Case(g, "%5.3d|", ["-7"]),
    ]
    flags = ["", "-", "0", "+", " ", "#"]
    for _ in range(60):
        conv = rng.choice("diuxX")
        fl = rng.choice(flags)
        if fl == "#" and conv not in "xX":
            fl = ""
        if fl in "+ " and conv not in "di":
            fl = ""
        w = rng.choice(["", "1", "4", "9", "12"])
        prec = rng.choice(["", ".", ".0", ".2", ".7"])
        if conv in "di":
            v = str(rng.randint(-99999, 99999))
        else:
            v = f"{rng.randint(0, 2 ** 32 - 1)}U"
        cases.append(Case(g, f"[%{fl}{w}{prec}{conv}]", [v], title="random"))
    return cases
