/* Part 1 - libc functions. */
#define _GNU_SOURCE
#include "tests.h"
#include <ctype.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------------------------------------------------------------------- */
/* helpers                                                                 */
/* ---------------------------------------------------------------------- */

/* Compare two buffers; on mismatch show the bytes around the difference. */
static int	mem_report(const char *what, const void *exp, const void *got,
				size_t n)
{
	const unsigned char	*e = exp;
	const unsigned char	*g = got;
	size_t				i;
	size_t				from;
	size_t				len;

	for (i = 0; i < n && e[i] == g[i]; i++)
		;
	if (i == n)
		return (1);
	from = i > 8 ? i - 8 : 0;
	len = n - from > 32 ? 32 : n - from;
	nx_expect(nx_f("%s = %s", what, nx_m(e + from, len)),
		nx_f("%s = %s", what, nx_m(g + from, len)));
	nx_note("first difference at byte %zu (showing bytes %zu..%zu)", i,
		from, from + len - 1);
	return (0);
}

static int	sign(long v)
{
	return ((v > 0) - (v < 0));
}

static const char	*cname(int c)
{
	if (c == -1)
		return ("EOF (-1)");
	if (c >= 0 && c <= 255)
		return (nx_f("%d %s", c, nx_ch(c)));
	return (nx_f("%d", c));
}

/* ---------------------------------------------------------------------- */
/* ctype                                                                   */
/* ---------------------------------------------------------------------- */

static int	ref_isascii(int c)
{
	return (c >= 0 && c <= 127);
}

/* want: 1 = chars in the class, 0 = chars outside, -1 = everything */
static void	ctype_case(const char *name, int (*ft)(int), int (*ref)(int),
				int lo, int hi, int want, const char *label)
{
	long	lc;
	int		c;
	int		bad;
	int		got;
	int		exp;
	int		not01;

	if (!nx_case("%s(c) for %s", name, label))
		return ;
	bad = 0;
	not01 = 0;
	for (lc = lo; lc <= hi; lc++)
	{
		c = (int)lc;
		exp = ref(c) ? 1 : 0;
		if (want >= 0 && exp != want)
			continue ;
		nx_ctx("%s(%s)", name, cname(c));
		got = ft(c);
		if (got == exp)
			continue ;
		if ((got != 0) == (exp != 0))
			not01 = 1;
		if (bad == 0)
			nx_expect(nx_f("%s(%s) == %d", name, cname(c), exp),
				nx_f("%d", got));
		else if (bad < 6)
			nx_note("also %s(%s): expected %d, got %d", name, cname(c),
				exp, got);
		bad++;
	}
	if (bad >= 6)
		nx_note("%d wrong values in total", bad);
	if (not01)
		nx_note("subject v1.3: must return exactly 1 or 0");
	nx_end();
}

static void	ctype_all(const char *name, int (*ft)(int), int (*ref)(int),
				const char *cls)
{
	ctype_case(name, ft, ref, 0, 127, 1, nx_f("%s chars", cls));
	ctype_case(name, ft, ref, 0, 127, 0, nx_f("non %s chars (0..127)", cls));
	ctype_case(name, ft, ref, 128, 255, -1, "128..255 (not ASCII: 0)");
	ctype_case(name, ft, ref, -1, -1, -1, "EOF (-1)");
}

void	t_isalpha(void)
{
	ctype_all("ft_isalpha", ft_isalpha, isalpha, "alpha");
}

void	t_isdigit(void)
{
	ctype_all("ft_isdigit", ft_isdigit, isdigit, "digit");
}

void	t_isalnum(void)
{
	ctype_all("ft_isalnum", ft_isalnum, isalnum, "alnum");
}

void	t_isprint(void)
{
	ctype_all("ft_isprint", ft_isprint, isprint, "printable");
}

void	t_isascii(void)
{
	ctype_case("ft_isascii", ft_isascii, ref_isascii, 0, 127, 1, "0..127");
	ctype_case("ft_isascii", ft_isascii, ref_isascii, 128, 1000, 0,
		"128..1000");
	ctype_case("ft_isascii", ft_isascii, ref_isascii, -1000, -1, 0,
		"-1000..-1");
	ctype_case("ft_isascii", ft_isascii, ref_isascii, INT_MIN, INT_MIN, 0,
		"INT_MIN");
	ctype_case("ft_isascii", ft_isascii, ref_isascii, INT_MAX, INT_MAX, 0,
		"INT_MAX");
}

static void	case_conv(const char *name, int (*ft)(int), int (*ref)(int),
				int lo, int hi, const char *label)
{
	int	c;
	int	bad;
	int	got;

	if (!nx_case("%s(c) for %s", name, label))
		return ;
	bad = 0;
	for (c = lo; c <= hi; c++)
	{
		nx_ctx("%s(%s)", name, cname(c));
		got = ft(c);
		if (got == ref(c))
			continue ;
		if (bad == 0)
			nx_expect(nx_f("%s(%s) == %d", name, cname(c), ref(c)),
				nx_f("%d", got));
		else if (bad < 6)
			nx_note("also %s(%s): expected %d, got %d", name, cname(c),
				ref(c), got);
		bad++;
	}
	if (bad >= 6)
		nx_note("%d wrong values in total", bad);
	nx_end();
}

void	t_toupper(void)
{
	case_conv("ft_toupper", ft_toupper, toupper, 'a', 'z', "'a'..'z'");
	case_conv("ft_toupper", ft_toupper, toupper, 0, 'a' - 1, "0..'a'-1");
	case_conv("ft_toupper", ft_toupper, toupper, 'z' + 1, 255, "'z'+1..255");
	case_conv("ft_toupper", ft_toupper, toupper, -1, -1, "EOF (-1)");
}

void	t_tolower(void)
{
	case_conv("ft_tolower", ft_tolower, tolower, 'A', 'Z', "'A'..'Z'");
	case_conv("ft_tolower", ft_tolower, tolower, 0, 'A' - 1, "0..'A'-1");
	case_conv("ft_tolower", ft_tolower, tolower, 'Z' + 1, 255, "'Z'+1..255");
	case_conv("ft_tolower", ft_tolower, tolower, -1, -1, "EOF (-1)");
}

/* ---------------------------------------------------------------------- */
/* strlen                                                                  */
/* ---------------------------------------------------------------------- */

static void	strlen_case(const char *s, const char *label)
{
	size_t	got;

	if (!nx_case("ft_strlen(%s)", label ? label : nx_s(s)))
		return ;
	got = ft_strlen(s);
	if (got != strlen(s))
		nx_expect(nx_f("%zu", strlen(s)), nx_f("%zu", got));
	nx_end();
}

void	t_strlen(void)
{
	static char	big[100001];

	strlen_case("", NULL);
	strlen_case("a", NULL);
	strlen_case("hello world", NULL);
	strlen_case("ab\0cd", "\"ab\\0cd\" (\\0 in the middle)");
	strlen_case("\x80\xff\xe9t\xc3\xa9", NULL);
	strlen_case("\t\n\v\f\r ", NULL);
	memset(big, 'x', 100000);
	strlen_case(big, "<100000 x 'x'>");
}

/* ---------------------------------------------------------------------- */
/* memset / bzero / memcpy / memmove                                      */
/* ---------------------------------------------------------------------- */

#define BUF 256
#define OFF 32

static void	memset_case(int c, size_t n, const char *label)
{
	unsigned char	exp[BUF];
	unsigned char	got[BUF];
	void			*r;

	if (!nx_case("ft_memset(buf, %s, %zu)%s", label, n,
			n ? "" : " (nothing written)"))
		return ;
	memset(exp, 0xAA, BUF);
	memset(got, 0xAA, BUF);
	memset(exp + OFF, c, n);
	r = ft_memset(got + OFF, c, n);
	if (r != got + OFF)
		nx_expect("return == buf", nx_ptr(r, got + OFF));
	else
		mem_report("buffer", exp, got, BUF);
	nx_end();
}

static void	memset_big(void)
{
	unsigned char	*exp;
	unsigned char	*got;
	size_t			n;

	n = 1 << 20;
	if (!nx_case("ft_memset(buf, 'z', %zu) (1 MiB)", n))
		return ;
	exp = nx_raw_malloc(n + 64);
	got = nx_raw_malloc(n + 64);
	memset(exp, 0xAA, n + 64);
	memset(got, 0xAA, n + 64);
	memset(exp, 'z', n);
	ft_memset(got, 'z', n);
	mem_report("buffer", exp, got, n + 64);
	nx_raw_free(exp);
	nx_raw_free(got);
	nx_end();
}

void	t_memset(void)
{
	memset_case('A', 0, "'A'");
	memset_case('A', 1, "'A'");
	memset_case('A', 10, "'A'");
	memset_case(0, 42, "0");
	memset_case(-1, 16, "-1 (byte 0xff)");
	memset_case(-128, 16, "-128 (byte 0x80)");
	memset_case(0x141, 8, "0x141 (byte 0x41 'A')");
	memset_case(200, 100, "200");
	memset_case('x', BUF - OFF, "'x'");
	memset_big();
}

static void	bzero_case(size_t n)
{
	unsigned char	exp[BUF];
	unsigned char	got[BUF];

	if (!nx_case("ft_bzero(buf, %zu)%s", n, n ? "" : " (nothing written)"))
		return ;
	memset(exp, 0xAA, BUF);
	memset(got, 0xAA, BUF);
	memset(exp + OFF, 0, n);
	ft_bzero(got + OFF, n);
	mem_report("buffer", exp, got, BUF);
	nx_end();
}

void	t_bzero(void)
{
	unsigned char	*got;
	size_t			i;

	bzero_case(0);
	bzero_case(1);
	bzero_case(5);
	bzero_case(100);
	bzero_case(BUF - OFF);
	if (nx_case("ft_bzero(buf, 1048576) (1 MiB)"))
	{
		got = nx_raw_malloc((1 << 20) + 16);
		memset(got, 0xAA, (1 << 20) + 16);
		ft_bzero(got, 1 << 20);
		for (i = 0; i < (1 << 20) && got[i] == 0; i++)
			;
		if (i < (1 << 20))
			nx_ko("byte %zu not set to 0", i);
		else if (got[1 << 20] != 0xAA)
			nx_ko("wrote past the end (byte %d)", 1 << 20);
		nx_raw_free(got);
		nx_end();
	}
}

static void	memcpy_case(const void *src, size_t n, const char *label)
{
	unsigned char	exp[BUF];
	unsigned char	got[BUF];
	void			*r;

	if (!nx_case("ft_memcpy(dst, %s, %zu)", label ? label : nx_m(src, n), n))
		return ;
	memset(exp, 0xAA, BUF);
	memset(got, 0xAA, BUF);
	memcpy(exp + OFF, src, n);
	r = ft_memcpy(got + OFF, src, n);
	if (r != got + OFF)
		nx_expect("return == dst", nx_ptr(r, got + OFF));
	else
		mem_report("dst buffer", exp, got, BUF);
	nx_end();
}

void	t_memcpy(void)
{
	char	src[200];
	int		i;

	for (i = 0; i < 200; i++)
		src[i] = (char)(i * 7 + 3);
	memcpy_case("hello", 0, "\"hello\"");
	memcpy_case("hello", 3, "\"hello\"");
	memcpy_case("hello", 6, "\"hello\"");
	memcpy_case("ab\0cd", 5, NULL);
	memcpy_case("\x80\xff\x01\xfe", 4, NULL);
	memcpy_case(src, 200, "<200 bytes 0x03 0x0a ...>");
	if (nx_case("ft_memcpy(dst, src, 1048576) (1 MiB)"))
	{
		char	*a = nx_raw_malloc(1 << 20);
		char	*b = nx_raw_malloc((1 << 20) + 8);

		for (i = 0; i < (1 << 20); i++)
			a[i] = (char)(i ^ (i >> 8));
		memset(b, 0xAA, (1 << 20) + 8);
		ft_memcpy(b, a, 1 << 20);
		mem_report("dst", a, b, 1 << 20);
		if ((unsigned char)b[1 << 20] != 0xAA)
			nx_ko("wrote past n bytes");
		nx_raw_free(a);
		nx_raw_free(b);
		nx_end();
	}
}

/* memmove inside one buffer: dst = buf + d, src = buf + s */
static void	memmove_case(size_t d, size_t s, size_t n, const char *label)
{
	unsigned char	exp[BUF * 2];
	unsigned char	got[BUF * 2];
	void			*r;
	size_t			i;

	if (!nx_case("ft_memmove(buf + %zu, buf + %zu, %zu) %s", d, s, n, label))
		return ;
	for (i = 0; i < sizeof(exp); i++)
		exp[i] = (unsigned char)('a' + i % 26);
	exp[5] = 0;
	exp[40] = 0x80;
	memcpy(got, exp, sizeof(exp));
	memmove(exp + d, exp + s, n);
	r = ft_memmove(got + d, got + s, n);
	if (r != got + d)
		nx_expect("return == dst", nx_ptr(r, got));
	else
		mem_report("buf", exp, got, sizeof(exp));
	nx_end();
}

static void	memmove_big(int forward)
{
	size_t			n;
	unsigned char	*a;
	unsigned char	*b;
	size_t			i;

	n = 100000;
	if (!nx_case("ft_memmove(buf + %d, buf + %d, %zu) (big %s overlap)",
			forward ? 1000 : 0, forward ? 0 : 1000, n,
			forward ? "forward" : "backward"))
		return ;
	a = nx_raw_malloc(n + 2000);
	b = nx_raw_malloc(n + 2000);
	for (i = 0; i < n + 2000; i++)
		a[i] = (unsigned char)(i * 31 + (i >> 7));
	memcpy(b, a, n + 2000);
	if (forward)
	{
		memmove(a + 1000, a, n);
		ft_memmove(b + 1000, b, n);
	}
	else
	{
		memmove(a, a + 1000, n);
		ft_memmove(b, b + 1000, n);
	}
	mem_report("buf", a, b, n + 2000);
	nx_raw_free(a);
	nx_raw_free(b);
	nx_end();
}

void	t_memmove(void)
{
	memmove_case(0, 100, 0, "(n = 0)");
	memmove_case(0, 100, 20, "(no overlap, dst < src)");
	memmove_case(100, 0, 20, "(no overlap, dst > src)");
	memmove_case(2, 0, 10, "(forward overlap: dst > src)");
	memmove_case(1, 0, 30, "(forward overlap by 1)");
	memmove_case(0, 2, 10, "(backward overlap: dst < src)");
	memmove_case(0, 1, 30, "(backward overlap by 1)");
	memmove_case(10, 10, 20, "(dst == src)");
	memmove_case(3, 0, 50, "(overlap, crosses a \\0 byte)");
	memmove_case(0, 3, 50, "(overlap, crosses a \\0 byte)");
	memmove_big(1);
	memmove_big(0);
}

/* ---------------------------------------------------------------------- */
/* strlcpy / strlcat (BSD reference, glibc may not have them)             */
/* ---------------------------------------------------------------------- */

static size_t	ref_strlcpy(char *dst, const char *src, size_t size)
{
	size_t	len;
	size_t	n;

	len = strlen(src);
	if (size)
	{
		n = len < size - 1 ? len : size - 1;
		memcpy(dst, src, n);
		dst[n] = 0;
	}
	return (len);
}

static size_t	ref_strlcat(char *dst, const char *src, size_t size)
{
	size_t	dl;

	dl = strnlen(dst, size);
	if (dl == size)
		return (size + strlen(src));
	return (dl + ref_strlcpy(dst + dl, src, size - dl));
}

/* dst starts as `init` (NUL terminated) inside a 0xAA filled buffer. Bytes
 * at index >= size must never change; bytes after the terminator inside
 * size are free (BSD does not pad). */
static void	lcpy_case(int cat, const char *init, const char *src, size_t size,
				const char *label)
{
	char	exp[BUF];
	char	got[BUF];
	size_t	re;
	size_t	rg;
	size_t	cmp;

	if (!nx_case("ft_strl%s(dst = %s, %s, %zu)%s%s", cat ? "cat" : "cpy",
			nx_s(init), nx_s(src), size, label ? " " : "",
			label ? label : ""))
		return ;
	memset(exp, 0xAA, BUF);
	memset(got, 0xAA, BUF);
	memcpy(exp, init, strlen(init) + 1);
	memcpy(got, init, strlen(init) + 1);
	if (cat)
	{
		re = ref_strlcat(exp, src, size);
		rg = ft_strlcat(got, src, size);
	}
	else
	{
		re = ref_strlcpy(exp, src, size);
		rg = ft_strlcpy(got, src, size);
	}
	if (rg != re)
	{
		nx_expect(nx_f("return %zu", re), nx_f("return %zu", rg));
		if (cat && size <= strlen(init))
			nx_note("size <= strlen(dst): must return size + strlen(src)");
		else
			nx_note(cat ? "must return strlen(initial dst) + strlen(src)"
				: "must return strlen(src)");
	}
	cmp = strlen(exp) + 1;
	if (cmp > BUF)
		cmp = BUF;
	if (strcmp(exp, got) != 0 && size > 0)
		nx_expect(nx_f("dst = %s", nx_s(exp)),
			nx_f("dst = %s", nx_m(got, strnlen(got, BUF - 1))));
	else if (memcmp(exp + size, got + size, BUF - size) != 0)
		nx_ko("wrote past size: byte %zu+ of dst changed", size);
	else if (size == 0 && memcmp(exp, got, BUF))
		nx_ko("size is 0: dst must not be touched (got %s)",
			nx_m(got, 8));
	(void)cmp;
	nx_end();
}

void	t_strlcpy(void)
{
	lcpy_case(0, "XXXXXXXX", "hello", 0, "(size 0: nothing written)");
	lcpy_case(0, "XXXXXXXX", "hello", 1, "(only the \\0)");
	lcpy_case(0, "XXXXXXXX", "hello", 3, "(truncate)");
	lcpy_case(0, "XXXXXXXX", "hello", 5, "(size == len: truncate)");
	lcpy_case(0, "XXXXXXXX", "hello", 6, "(exact fit)");
	lcpy_case(0, "XXXXXXXX", "hello", 20, NULL);
	lcpy_case(0, "XXXXXXXX", "", 10, "(empty src)");
	lcpy_case(0, "XXXXXXXX", "", 0, "(empty src, size 0)");
	lcpy_case(0, "XXXXXXXX", "ab\0cd", 10, "(stops at \\0)");
	lcpy_case(0, "XXXXXXXX", "\x80\xff\xe9", 10, NULL);
	lcpy_case(0, "", "lorem ipsum dolor sit amet", 15, NULL);
}

void	t_strlcat(void)
{
	lcpy_case(1, "abc", "hello", 0, "(size 0: returns strlen(src))");
	lcpy_case(1, "abc", "hello", 2, "(size < strlen(dst))");
	lcpy_case(1, "abc", "hello", 3, "(size == strlen(dst))");
	lcpy_case(1, "abc", "hello", 4, "(no room to append)");
	lcpy_case(1, "abc", "hello", 6, "(partial append)");
	lcpy_case(1, "abc", "hello", 8, "(size == total + 0: truncate 1)");
	lcpy_case(1, "abc", "hello", 9, "(exact fit)");
	lcpy_case(1, "abc", "hello", 50, "(full append)");
	lcpy_case(1, "", "hello", 50, "(empty dst)");
	lcpy_case(1, "abc", "", 50, "(empty src)");
	lcpy_case(1, "", "", 1, NULL);
	lcpy_case(1, "abcdefghij", "xyz", 5, "(no \\0 in the first size bytes)");
	lcpy_case(1, "pqrs", "\x80\xff", 10, NULL);
	lcpy_case(1, "rrrrrr", "lorem ipsum dolor sit amet", 15, NULL);
}

/* ---------------------------------------------------------------------- */
/* strchr / strrchr / memchr                                              */
/* ---------------------------------------------------------------------- */

static void	chr_case(int last, const char *s, int c, const char *clabel)
{
	const char	*exp;
	const char	*got;

	if (!nx_case("ft_str%schr(%s, %s)", last ? "r" : "", nx_s(s),
			clabel ? clabel : nx_ch(c)))
		return ;
	exp = last ? strrchr(s, c) : strchr(s, c);
	got = last ? ft_strrchr(s, c) : ft_strchr(s, c);
	if (exp != got)
		nx_expect(nx_ptr(exp, s), nx_ptr(got, s));
	nx_end();
}

static void	chr_all(int last)
{
	const char	*s = "bonjour le monde";

	chr_case(last, s, 'b', NULL);
	chr_case(last, s, 'o', NULL);
	chr_case(last, s, 'e', NULL);
	chr_case(last, s, 'z', NULL);
	chr_case(last, s, '\0', NULL);
	chr_case(last, s, 'o' + 256, "'o' + 256");
	chr_case(last, s, '\0' + 256, "256 (== '\\0' as char)");
	chr_case(last, "", 'a', NULL);
	chr_case(last, "", '\0', NULL);
	chr_case(last, "ab\0cd", 'c', NULL);
	chr_case(last, "t\xe9t\xe9", 0xe9, "0xe9");
	chr_case(last, "t\xe9t\xe9", (char)0xe9, "(char)0xe9 (negative)");
	chr_case(last, "aaaa", 'a', NULL);
}

void	t_strchr(void)
{
	chr_all(0);
}

void	t_strrchr(void)
{
	chr_all(1);
}

static void	memchr_case(const void *s, size_t slen, int c, size_t n,
				const char *clabel)
{
	const void	*exp;
	const void	*got;

	if (!nx_case("ft_memchr(%s, %s, %zu)", nx_m(s, slen),
			clabel ? clabel : nx_ch(c), n))
		return ;
	exp = memchr(s, c, n);
	got = ft_memchr(s, c, n);
	if (exp != got)
		nx_expect(nx_ptr(exp, s), nx_ptr(got, s));
	nx_end();
}

void	t_memchr(void)
{
	const char	t[] = "abc\0def\x80\xff";

	memchr_case(t, 10, 'c', 10, NULL);
	memchr_case(t, 10, 'a', 10, NULL);
	memchr_case(t, 10, 'e', 10, "'e' (after a \\0 byte)");
	memchr_case(t, 10, '\0', 10, NULL);
	memchr_case(t, 10, 'z', 10, NULL);
	memchr_case(t, 10, 'e', 4, "'e' (beyond n)");
	memchr_case(t, 10, 'a', 0, "'a' (n = 0)");
	memchr_case(t, 10, 0x80, 10, "0x80");
	memchr_case(t, 10, -1, 10, "-1 (byte 0xff)");
	memchr_case(t, 10, 'c' + 256, 10, "'c' + 256");
}

/* ---------------------------------------------------------------------- */
/* strncmp / memcmp                                                        */
/* ---------------------------------------------------------------------- */

static const char	*sgn(int v)
{
	return (v < 0 ? "< 0" : v > 0 ? "> 0" : "0");
}

static void	ncmp_case(int mem, const char *a, size_t al, const char *b,
				size_t bl, size_t n, const char *label)
{
	int	exp;
	int	got;

	if (!nx_case("ft_%s(%s, %s, %s)%s%s", mem ? "memcmp" : "strncmp",
			mem ? nx_m(a, al) : nx_s(a), mem ? nx_m(b, bl) : nx_s(b),
			n == SIZE_MAX ? "SIZE_MAX" : nx_f("%zu", n), label ? " " : "",
			label ? label : ""))
		return ;
	exp = mem ? memcmp(a, b, n) : strncmp(a, b, n);
	got = mem ? ft_memcmp(a, b, n) : ft_strncmp(a, b, n);
	if (sign(exp) != sign(got))
	{
		nx_expect(sgn(exp), nx_f("%d", got));
		if ((unsigned char)a[0] > 127 || (unsigned char)b[0] > 127
			|| strchr(label ? label : "", 'u'))
			nx_note("bytes must be compared as unsigned char");
	}
	nx_end();
}

void	t_strncmp(void)
{
	ncmp_case(0, "abcdef", 0, "abcdef", 0, 6, "(same strings)");
	ncmp_case(0, "abcdef", 0, "abcdeg", 0, 6, "(different ending, left)");
	ncmp_case(0, "abcdeg", 0, "abcdef", 0, 6, "(different ending, right)");
	ncmp_case(0, "abc", 0, "xyz", 0, 3, "(different strings)");
	ncmp_case(0, "abcdef", 0, "abcxyz", 0, 3, "(n smaller than diff pos)");
	ncmp_case(0, "abc", 0, "xyz", 0, 0, "(n = 0)");
	ncmp_case(0, "abc", 0, "abc", 0, 100, "(n larger, equal strings)");
	ncmp_case(0, "abc", 0, "abd", 0, 100, "(n larger, different)");
	ncmp_case(0, "abc", 0, "ab", 0, 3, "(s2 shorter)");
	ncmp_case(0, "ab", 0, "abc", 0, 5, "(s1 shorter)");
	ncmp_case(0, "", 0, "", 0, 5, "(both empty)");
	ncmp_case(0, "", 0, "a", 0, 1, NULL);
	ncmp_case(0, "ab\0x", 0, "ab\0y", 0, 5, "(must stop at \\0)");
	ncmp_case(0, "test\200", 0, "test\0", 0, 6, "(unsigned char)");
	ncmp_case(0, "\200", 0, "\0", 0, 1, "(unsigned char)");
	ncmp_case(0, "\x01", 0, "\xff", 0, 1, "(unsigned char)");
	ncmp_case(0, "abc", 0, "abd", 0, SIZE_MAX, "(n = SIZE_MAX)");
	ncmp_case(0, "abc", 0, "abc", 0, SIZE_MAX, "(n = SIZE_MAX)");
}

void	t_memcmp(void)
{
	ncmp_case(1, "abc", 3, "xyz", 3, 0, "(n = 0)");
	ncmp_case(1, "abcdef", 6, "abcdef", 6, 6, "(equal)");
	ncmp_case(1, "abcdeg", 6, "abcdef", 6, 6, "(left greater)");
	ncmp_case(1, "abcdef", 6, "abcdeg", 6, 6, "(right greater)");
	ncmp_case(1, "ab\0c", 4, "ab\0d", 4, 4, "(must not stop at \\0)");
	ncmp_case(1, "\200", 1, "\0", 1, 1, "(unsigned char)");
	ncmp_case(1, "\x01", 1, "\xff", 1, 1, "(unsigned char)");
	ncmp_case(1, "abcX", 4, "abcY", 4, 3, "(difference beyond n)");
	ncmp_case(1, "a", 1, "b", 1, 1, NULL);
}

/* ---------------------------------------------------------------------- */
/* strnstr                                                                 */
/* ---------------------------------------------------------------------- */

static char	*ref_strnstr(const char *h, const char *nd, size_t len)
{
	size_t	nl;

	nl = strlen(nd);
	if (nl == 0)
		return ((char *)h);
	while (*h && len >= nl)
	{
		if (*h == *nd && strncmp(h, nd, nl) == 0)
			return ((char *)h);
		h++;
		len--;
	}
	return (NULL);
}

static void	nstr_case(const char *h, const char *nd, size_t len,
				const char *label)
{
	char	*exp;
	char	*got;

	if (!nx_case("ft_strnstr(%s, %s, %s)%s%s", nx_s(h), nx_s(nd),
			len == SIZE_MAX ? "SIZE_MAX" : nx_f("%zu", len),
			label ? " " : "", label ? label : ""))
		return ;
	exp = ref_strnstr(h, nd, len);
	got = ft_strnstr(h, nd, len);
	if (exp != got)
		nx_expect(nx_ptr(exp, h), nx_ptr(got, h));
	nx_end();
}

void	t_strnstr(void)
{
	const char	*h = "lorem ipsum dolor sit amet";

	nstr_case(h, "ipsum", 30, "(found)");
	nstr_case(h, "lorem", 30, "(at start)");
	nstr_case(h, "amet", 30, "(at end)");
	nstr_case(h, "consectetur", 30, "(not found)");
	nstr_case(h, "", 30, "(empty needle: haystack)");
	nstr_case(h, "", 0, "(empty needle, len 0)");
	nstr_case("", "", 0, NULL);
	nstr_case("", "a", 5, NULL);
	nstr_case(h, "lorem", 0, "(len 0)");
	nstr_case(h, "ipsum", 10, "(needle ends past len)");
	nstr_case(h, "ipsum", 11, "(needle ends exactly at len)");
	nstr_case(h, "dolor", 15, "(needle starts before len, ends after)");
	nstr_case("aaabcabcd", "aabc", 20, "(partial match first)");
	nstr_case("aaabcabcd", "abcd", 9, NULL);
	nstr_case("aaabcabcd", "cd", 8, "(not within len)");
	nstr_case("abc", "abcdef", 10, "(needle longer than haystack)");
	nstr_case("ab\0cd", "cd", 5, "(stops at \\0 of haystack)");
	nstr_case(h, "sit", SIZE_MAX, "(len = SIZE_MAX)");
	nstr_case("1", "a", 1, NULL);
	nstr_case("22", "b", 2, NULL);
	nstr_case("\xe9t\xe9", "t\xe9", 3, NULL);
}

/* ---------------------------------------------------------------------- */
/* atoi                                                                    */
/* ---------------------------------------------------------------------- */

static void	atoi_case(const char *s)
{
	int	exp;
	int	got;

	if (!nx_case("ft_atoi(%s)", nx_s(s)))
		return ;
	exp = atoi(s);
	got = ft_atoi(s);
	if (exp != got)
		nx_expect(nx_f("%d (libc atoi)", exp), nx_f("%d", got));
	nx_end();
}

void	t_atoi(void)
{
	static const char	*cases[] = {
		"0", "42", "-42", "+42", "7", "-0", "+0", "00000042", "-0000042",
		"2147483647", "-2147483648", "  \t\n\v\f\r 42", " \t\n\v\f\r-42",
		"--42", "+-42", "-+42", "++42", "- 42", "+ 42", "42abc", "4 2",
		"12-34", "abc42", "", " ", "-", "+", "\x1b" "42", "\x80" "42",
		"\x7f" "42", "   -0012a3", "1a", "999", "-1", "+2147483647",
		"\n\n\n  -46\b9 \n5d6", "  +000000000000000000000000000012",
		"-2147483647", "1844674407", "  -2147483648abc", NULL};
	char				buf[32];
	unsigned int		seed;
	int					i;

	for (i = 0; cases[i]; i++)
		atoi_case(cases[i]);
	seed = 42;
	for (i = 0; i < 8; i++)
	{
		seed = seed * 1103515245u + 12345u;
		snprintf(buf, sizeof(buf), "%d", (int)seed);
		atoi_case(buf);
	}
}

/* ---------------------------------------------------------------------- */
/* calloc / strdup                                                         */
/* ---------------------------------------------------------------------- */

static const char	*szname(size_t v)
{
	if (v == SIZE_MAX)
		return ("SIZE_MAX");
	if (v == SIZE_MAX / 2 + 1)
		return ("SIZE_MAX / 2 + 1");
	if (v == (size_t)INT_MAX)
		return ("INT_MAX");
	if (v > (size_t)1 << 62)
		return (nx_f("(size_t)%ld", (long)v));
	return (nx_f("%zu", v));
}

static void	calloc_case(size_t nm, size_t sz)
{
	unsigned char	*p;
	size_t			i;
	size_t			total;

	if (!nx_case("ft_calloc(%s, %s)", szname(nm), szname(sz)))
		return ;
	total = nm * sz;
	nx_track(1);
	p = ft_calloc(nm, sz);
	nx_track(0);
	if (!p)
	{
		nx_expect("a unique pointer that can be freed",
			"NULL");
		if (total == 0)
			nx_note("subject v1.3: if nmemb or size is 0, calloc returns "
				"a unique pointer that free() accepts");
		nx_end();
		return ;
	}
	if (total == 0)
	{
		if (nx_block_size(p) < 0)
			nx_ko("returned pointer is not from malloc");
	}
	else
		nx_size_check(p, total, "result");
	for (i = 0; i < total && p[i] == 0; i++)
		;
	if (i < total)
		nx_ko("memory not set to 0: byte %zu = 0x%02x", i, p[i]);
	nx_free(p);
	nx_end();
}

static void	calloc_overflow(size_t nm, size_t sz)
{
	void	*p;

	if (!nx_case("ft_calloc(%s, %s) (nmemb * size overflows / too big)",
			szname(nm), szname(sz)))
		return ;
	nx_track(1);
	p = ft_calloc(nm, sz);
	nx_track(0);
	if (p)
	{
		nx_expect("NULL", "a pointer");
		if (nx_malloc_calls())
			nx_note("malloc was called with %zu bytes: nmemb * size "
				"overflowed (= %zu)", nx_last_request(), nm * sz);
		nx_free(p);
	}
	nx_end();
}

static void	calloc_mf(long n)
{
	void	*p;

	nx_fail_at(n);
	nx_track(1);
	p = ft_calloc(5, 4);
	nx_track(0);
	if (p)
	{
		nx_expect("NULL", "a pointer");
		nx_free(p);
	}
}

void	t_calloc(void)
{
	calloc_case(2, 2);
	calloc_case(1, 1);
	calloc_case(10, sizeof(int));
	calloc_case(123, 7);
	calloc_case(1 << 20, 1);
	calloc_case(0, 0);
	calloc_case(0, 5);
	calloc_case(5, 0);
	calloc_case(0, SIZE_MAX);
	calloc_case(SIZE_MAX, 0);
	calloc_overflow(SIZE_MAX, SIZE_MAX);
	calloc_overflow(SIZE_MAX, 2);
	calloc_overflow(SIZE_MAX / 2 + 1, 2);
	calloc_overflow(2, SIZE_MAX / 2 + 1);
	calloc_overflow((size_t)1 << 33, (size_t)1 << 33);
	calloc_overflow((size_t)-5, (size_t)-5);
	calloc_overflow(3, (size_t)-5);
	calloc_overflow((size_t)-5, 3);
	calloc_overflow((size_t)INT_MAX, (size_t)INT_MAX);
	if (nx_case("ft_calloc(5, 4)"))
	{
		void	*p;

		nx_track(1);
		p = ft_calloc(5, 4);
		nx_track(0);
		nx_free(p);
		nx_end();
	}
	malloc_fail_series(nx_last_calls(), calloc_mf, "ft_calloc(5, 4)");
}

static void	strdup_case(const char *s, const char *label)
{
	char	*r;

	if (!nx_case("ft_strdup(%s)", label ? label : nx_s(s)))
		return ;
	nx_track(1);
	r = ft_strdup(s);
	nx_track(0);
	check_str_result(r, s, "result");
	if (r && r == s)
		nx_ko("returned the source pointer instead of a copy");
	else if (r)
	{
		nx_size_check(r, strlen(s) + 1, "result");
		nx_free(r);
	}
	nx_end();
}

static void	strdup_mf(long n)
{
	char	*r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_strdup("hello");
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", nx_s(r));
		nx_free(r);
	}
}

void	t_strdup(void)
{
	static char	big[5001];

	strdup_case("hello", NULL);
	strdup_case("", NULL);
	strdup_case("a", NULL);
	strdup_case("ab\0cd", "\"ab\\0cd\" (\\0 in the middle)");
	strdup_case("\x80\xff\xe9 \t\n", NULL);
	memset(big, 'q', 5000);
	strdup_case(big, "<5000 x 'q'>");
	strdup_case("hello", NULL);
	malloc_fail_series(nx_last_calls(), strdup_mf, "ft_strdup(\"hello\")");
}
