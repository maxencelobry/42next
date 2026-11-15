/* Part 2 - additional functions. */
#define _GNU_SOURCE
#include "tests.h"
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ---------------------------------------------------------------------- */
/* shared helpers                                                          */
/* ---------------------------------------------------------------------- */

static size_t	safe_len(const char *s)
{
	long	bs;

	bs = nx_block_size(s);
	if (bs >= 0)
		return (strnlen(s, (size_t)bs));
	return (strlen(s));
}

void	check_str_result(const char *got, const char *exp, const char *what)
{
	size_t	gl;
	long	bs;

	if (!got)
	{
		nx_expect(nx_s(exp), "NULL");
		return ;
	}
	gl = safe_len(got);
	bs = nx_block_size(got);
	if (bs >= 0 && gl == (size_t)bs)
	{
		nx_expect(nx_s(exp), nx_f("%s (not NUL terminated inside its %ld "
				"byte block)", nx_m(got, gl), bs));
		return ;
	}
	if (strcmp(got, exp) != 0)
		nx_expect(nx_s(exp), nx_s(got));
	(void)what;
}

#define MF_MAX 40

void	malloc_fail_series(long calls, void (*fn)(long n), const char *call)
{
	long	n;
	long	last;

	if (calls <= 0)
		return ;
	last = calls > MF_MAX ? MF_MAX : calls;
	for (n = 1; n <= last; n++)
	{
		if (nx_case("%s with malloc #%ld of %ld returning NULL", call, n,
				calls))
		{
			nx_ctx("%s, malloc #%ld returned NULL", call, n);
			fn(n);
			nx_end();
		}
	}
}

/* ---------------------------------------------------------------------- */
/* substr                                                                  */
/* ---------------------------------------------------------------------- */

static void	substr_case(const char *s, unsigned int start, size_t len,
				const char *label)
{
	char	*r;
	char	exp[256];
	size_t	sl;
	size_t	n;

	if (!nx_case("ft_substr(%s, %s, %s)%s%s", nx_s(s),
			start == UINT_MAX ? "UINT_MAX" : nx_f("%u", start),
			len == SIZE_MAX ? "SIZE_MAX" : nx_f("%zu", len),
			label ? " " : "", label ? label : ""))
		return ;
	sl = strlen(s);
	n = 0;
	if (start < sl)
		n = sl - start < len ? sl - start : len;
	memcpy(exp, s + (start < sl ? start : 0), n);
	exp[n] = 0;
	nx_track(1);
	r = ft_substr(s, start, len);
	nx_track(0);
	check_str_result(r, exp, "result");
	if (r)
	{
		nx_size_check(r, n + 1, "result");
		if (r == s + start)
			nx_ko("returned a pointer into s instead of a new string");
		else
			nx_free(r);
	}
	nx_end();
}

static void	substr_mf(long n)
{
	char	*r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_substr("hello world", 6, 5);
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", nx_s(r));
		nx_free(r);
	}
}

void	t_substr(void)
{
	substr_case("hello", 0, 5, "(full range)");
	substr_case("hello", 1, 3, NULL);
	substr_case("hello", 0, 0, "(len 0)");
	substr_case("hello", 4, 1, NULL);
	substr_case("hello", 5, 2, "(start == strlen)");
	substr_case("hello", 10, 3, "(start beyond the end)");
	substr_case("hello", UINT_MAX, 3, "(start beyond the end)");
	substr_case("", 0, 5, "(empty input)");
	substr_case("", 1, 1, NULL);
	substr_case("hello", 2, 100, "(len past the end: allocate only 4)");
	substr_case("hello", 2, SIZE_MAX, "(len = SIZE_MAX)");
	substr_case("hello", 0, SIZE_MAX, NULL);
	substr_case("lorem ipsum dolor", 6, 5, NULL);
	substr_case("ab\0cd", 1, 10, "(s = \"ab\\0cd\": stops at \\0)");
	substr_case("\xe9t\xe9", 1, 2, NULL);
	substr_case("hello world", 6, 5, NULL);
	malloc_fail_series(nx_last_calls(), substr_mf,
		"ft_substr(\"hello world\", 6, 5)");
}

/* ---------------------------------------------------------------------- */
/* strjoin                                                                 */
/* ---------------------------------------------------------------------- */

static void	strjoin_case(const char *a, const char *b, const char *label)
{
	char	*r;
	char	*exp;

	if (!nx_case("ft_strjoin(%s, %s)%s%s", nx_s(a), nx_s(b),
			label ? " " : "", label ? label : ""))
		return ;
	exp = nx_raw_malloc(strlen(a) + strlen(b) + 1);
	strcpy(exp, a);
	strcat(exp, b);
	nx_track(1);
	r = ft_strjoin(a, b);
	nx_track(0);
	check_str_result(r, exp, "result");
	if (r)
	{
		nx_size_check(r, strlen(exp) + 1, "result");
		if (r == a || r == b)
			nx_ko("returned one of the inputs instead of a new string");
		else
			nx_free(r);
	}
	nx_raw_free(exp);
	nx_end();
}

static void	strjoin_mf(long n)
{
	char	*r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_strjoin("hello ", "world");
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", nx_s(r));
		nx_free(r);
	}
}

void	t_strjoin(void)
{
	static char	big[3001];

	strjoin_case("hello ", "world", "(both non empty)");
	strjoin_case("", "world", "(left empty)");
	strjoin_case("hello", "", "(right empty)");
	strjoin_case("", "", "(both empty)");
	strjoin_case("\xe9t\xe9", "\x80\xff", NULL);
	strjoin_case("a\nb", "\tc", NULL);
	memset(big, 'k', 3000);
	strjoin_case(big, big, NULL);
	strjoin_case("hello ", "world", NULL);
	malloc_fail_series(nx_last_calls(), strjoin_mf,
		"ft_strjoin(\"hello \", \"world\")");
}

/* ---------------------------------------------------------------------- */
/* strtrim                                                                 */
/* ---------------------------------------------------------------------- */

static void	strtrim_case(const char *s, const char *set, const char *exp,
				const char *label)
{
	char	*r;

	if (!nx_case("ft_strtrim(%s, %s)%s%s", nx_s(s), nx_s(set),
			label ? " " : "", label ? label : ""))
		return ;
	nx_track(1);
	r = ft_strtrim(s, set);
	nx_track(0);
	check_str_result(r, exp, "result");
	if (r)
	{
		nx_size_check(r, strlen(exp) + 1, "result");
		if (r == s)
			nx_ko("returned s itself instead of a new string");
		else
			nx_free(r);
	}
	nx_end();
}

static void	strtrim_mf(long n)
{
	char	*r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_strtrim("  hello  ", " ");
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", nx_s(r));
		nx_free(r);
	}
}

void	t_strtrim(void)
{
	strtrim_case("  hello  ", " ", "hello", "(trim both sides)");
	strtrim_case("hello", " ", "hello", "(no trim)");
	strtrim_case("   hello", " ", "hello", "(left only)");
	strtrim_case("hello   ", " ", "hello", "(right only)");
	strtrim_case("xxhelloxx", "x", "hello", NULL);
	strtrim_case("abcHello Worldcba", "cba", "Hello World",
		"(set of several chars)");
	strtrim_case("a b a", "a ", "b", NULL);
	strtrim_case("ab  cd  ab", "ab", "  cd  ", "(inner chars kept)");
	strtrim_case("aaaa", "a", "", "(trim all)");
	strtrim_case("", "abc", "", "(empty input)");
	strtrim_case("  hi ", "", "  hi ", "(empty set)");
	strtrim_case("", "", "", NULL);
	strtrim_case("x", "x", "", NULL);
	strtrim_case("\t\n hi there\n \t", "\n\t ", "hi there", NULL);
	strtrim_case("\xe9\xe9t\xe9", "\xe9", "t", NULL);
	strtrim_case("lorem \n ipsum \t dolor \n sit \t amet", " ", "lorem \n "
		"ipsum \t dolor \n sit \t amet", NULL);
	strtrim_case("  hello  ", " ", "hello", NULL);
	malloc_fail_series(nx_last_calls(), strtrim_mf,
		"ft_strtrim(\"  hello  \", \" \")");
}

/* ---------------------------------------------------------------------- */
/* split                                                                   */
/* ---------------------------------------------------------------------- */

static const char	*arr_repr(char **a, int max)
{
	char	buf[1100];
	size_t	k;
	int		i;

	if (!a)
		return ("NULL");
	k = 0;
	buf[k++] = '{';
	for (i = 0; a[i] && i < max && k < 900; i++)
		k += snprintf(buf + k, sizeof(buf) - k, "%s, ", nx_s(a[i]));
	if (a[i] && i >= max)
		k += snprintf(buf + k, sizeof(buf) - k, "... ");
	snprintf(buf + k, sizeof(buf) - k, "NULL}");
	return (nx_f("%s", buf));
}

/* expected words from the reference split */
static int	ref_split(const char *s, char c, char words[][64], int max)
{
	int		n;
	size_t	len;

	n = 0;
	while (*s)
	{
		while (*s && *s == c)
			s++;
		if (!*s)
			break ;
		len = 0;
		while (s[len] && s[len] != c)
			len++;
		if (n < max)
		{
			memcpy(words[n], s, len < 63 ? len : 63);
			words[n][len < 63 ? len : 63] = 0;
		}
		n++;
		s += len;
	}
	return (n);
}

static void	free_split(char **r)
{
	int		i;
	long	bs;
	long	slots;

	if (!r)
		return ;
	bs = nx_block_size(r);
	slots = bs < 0 ? 1000000 : bs / (long)sizeof(char *);
	for (i = 0; i < slots && r[i]; i++)
		nx_free(r[i]);
	nx_free(r);
}

static void	split_case(const char *s, char c, const char *slabel)
{
	char	words[64][64];
	char	**r;
	int		n;
	int		i;
	long	bs;
	char	exp[1100];
	size_t	k;

	if (!nx_case("ft_split(%s, %s)", slabel ? slabel : nx_s(s), nx_ch(c)))
		return ;
	n = ref_split(s, c, words, 64);
	k = 0;
	k += snprintf(exp + k, sizeof(exp) - k, "{");
	for (i = 0; i < n && i < 8; i++)
		k += snprintf(exp + k, sizeof(exp) - k, "%s, ", nx_s(words[i]));
	snprintf(exp + k, sizeof(exp) - k, "%sNULL}", n > 8 ? "... " : "");
	nx_track(1);
	r = ft_split(s, c);
	nx_track(0);
	if (!r)
	{
		nx_expect(exp, "NULL");
		if (n == 0)
			nx_note("no word: must return an array containing only NULL");
		nx_end();
		return ;
	}
	bs = nx_block_size(r);
	if (bs < 0)
	{
		nx_ko("returned array is not a block from malloc");
		nx_no_leak_check();
		nx_end();
		return ;
	}
	for (i = 0; i < n; i++)
	{
		if ((long)((i + 1) * sizeof(char *)) > bs || !r[i])
		{
			nx_expect(exp, arr_repr(r, 8));
			nx_note("word %d missing (array of %ld bytes = %ld slots)", i,
				bs, bs / (long)sizeof(char *));
			break ;
		}
		if (i < 64 && strcmp(r[i], words[i]) != 0)
		{
			nx_expect(exp, arr_repr(r, 8));
			nx_note("word %d: expected %s, got %s", i, nx_s(words[i]),
				nx_s(r[i]));
			break ;
		}
	}
	if (!nx_failed())
	{
		if ((long)((n + 1) * sizeof(char *)) > bs)
			nx_ko("array has no room for the final NULL (%ld bytes for %d "
				"words)", bs, n);
		else if (r[n] != NULL)
			nx_expect(exp, nx_f("%s (array not NULL terminated after %d "
					"words)", arr_repr(r, 8), n));
		else
		{
			nx_size_check(r, (n + 1) * sizeof(char *), "array");
			for (i = 0; i < n; i++)
				if (!nx_size_check(r[i], strlen(words[i < 64 ? i : 63]) + 1,
						nx_f("word %d %s", i, nx_s(r[i]))))
					break ;
		}
	}
	if (!nx_failed())
		free_split(r);
	else
		nx_no_leak_check();
	nx_end();
}

static const char	*g_mf_s;
static char			g_mf_c;

static void	split_mf(long n)
{
	char	**r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_split(g_mf_s, g_mf_c);
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", arr_repr(r, 8));
		nx_note("subject: NULL if any allocation fails (and free what was "
			"already allocated)");
		free_split(r);
	}
}

static void	split_series(const char *s, char c)
{
	char	**r;

	if (nx_case("ft_split(%s, %s) (count mallocs)", nx_s(s), nx_ch(c)))
	{
		nx_track(1);
		r = ft_split(s, c);
		nx_track(0);
		free_split(r);
		nx_end();
	}
	g_mf_s = s;
	g_mf_c = c;
	malloc_fail_series(nx_last_calls(), split_mf,
		nx_f("ft_split(%s, %s)", nx_s(s), nx_ch(c)));
}

void	t_split(void)
{
	static char	big[6001];
	int			i;

	split_case("hello world", ' ', NULL);
	split_case("hello", ' ', "\"hello\" (one word)");
	split_case("  a  b ", ' ', NULL);
	split_case("   leading", ' ', NULL);
	split_case("trailing   ", ' ', NULL);
	split_case("", ' ', NULL);
	split_case("     ", ' ', "\"     \" (only separators)");
	split_case(",", ',', NULL);
	split_case("a,b,,c", ',', NULL);
	split_case(",,a,,b,,", ',', NULL);
	split_case("hello", '\0', "\"hello\" (separator \\0)");
	split_case("", '\0', NULL);
	split_case("a\nb\nc", '\n', NULL);
	split_case("hello\\nworld", ' ', NULL);
	split_case("hello world foo", 'o', NULL);
	split_case("^^^1^^2a,^^^^3^^^^--h^^^^", '^', NULL);
	split_case("  tab\tand\nnewline  ", ' ', NULL);
	split_case("\xe9" "a" "\xe9\xe9" "b" "\xe9", (char)0xe9, NULL);
	split_case("x", 'x', NULL);
	split_case("xy", 'x', NULL);
	split_case("lorem ipsum dolor sit amet, consectetur adipiscing elit. "
		"Sed non risus.", ' ', NULL);
	for (i = 0; i < 6000; i++)
		big[i] = (i % 3 == 2) ? ' ' : 'w';
	split_case(big, ' ', "<2000 words \"ww\">");
	split_series("hello world foo", ' ');
	split_series("  a b  ", ' ');
}

/* ---------------------------------------------------------------------- */
/* itoa                                                                    */
/* ---------------------------------------------------------------------- */

static void	itoa_case(int n)
{
	char	exp[32];
	char	*r;

	if (!nx_case("ft_itoa(%s)", n == INT_MIN ? "INT_MIN (-2147483648)"
			: n == INT_MAX ? "INT_MAX (2147483647)" : nx_f("%d", n)))
		return ;
	snprintf(exp, sizeof(exp), "%d", n);
	nx_track(1);
	r = ft_itoa(n);
	nx_track(0);
	check_str_result(r, exp, "result");
	if (r)
	{
		nx_size_check(r, strlen(exp) + 1, "result");
		nx_free(r);
	}
	nx_end();
}

static void	itoa_mf(long n)
{
	char	*r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_itoa(-1234);
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", nx_s(r));
		nx_free(r);
	}
}

void	t_itoa(void)
{
	static const int	v[] = {0, 1, -1, 9, -9, 10, -10, 42, -42, 99, 100,
		-100, 101, 1000000, -987654321, 123456789, 2147483646, -2147483647,
		INT_MAX, INT_MIN};
	unsigned int		seed;
	size_t				i;

	for (i = 0; i < sizeof(v) / sizeof(*v); i++)
		itoa_case(v[i]);
	seed = 7;
	for (i = 0; i < 6; i++)
	{
		seed = seed * 1103515245u + 12345u;
		itoa_case((int)seed);
	}
	itoa_case(-1234);
	malloc_fail_series(nx_last_calls(), itoa_mf, "ft_itoa(-1234)");
}

/* ---------------------------------------------------------------------- */
/* strmapi / striteri                                                      */
/* ---------------------------------------------------------------------- */

static unsigned int	g_idx[256];
static int			g_ncalls;
static char			*g_iter_s;
static int			g_badptr;

static char	map_plus_index(unsigned int i, char c)
{
	if (g_ncalls < 256)
		g_idx[g_ncalls] = i;
	g_ncalls++;
	return ((char)(c + i));
}

static char	map_upper(unsigned int i, char c)
{
	(void)i;
	g_ncalls++;
	return (c >= 'a' && c <= 'z' ? c - 32 : c);
}

static int	check_indices(int n)
{
	int	i;

	if (g_ncalls != n)
	{
		nx_ko("f called %d time(s), expected %d (once per char)", g_ncalls, n);
		return (0);
	}
	for (i = 0; i < n && i < 256; i++)
	{
		if (g_idx[i] != (unsigned int)i)
		{
			nx_ko("call %d of f got index %u, expected %d", i, g_idx[i], i);
			return (0);
		}
	}
	return (1);
}

static void	strmapi_case(const char *s, int upper, const char *label)
{
	char	exp[256];
	char	*r;
	size_t	i;

	if (!nx_case("ft_strmapi(%s, %s)", nx_s(s), label))
		return ;
	for (i = 0; s[i]; i++)
		exp[i] = upper ? (s[i] >= 'a' && s[i] <= 'z' ? s[i] - 32 : s[i])
			: (char)(s[i] + i);
	exp[i] = 0;
	g_ncalls = 0;
	nx_track(1);
	r = ft_strmapi(s, upper ? map_upper : map_plus_index);
	nx_track(0);
	check_str_result(r, exp, "result");
	if (!upper)
		check_indices(strlen(s));
	if (r)
	{
		nx_size_check(r, strlen(s) + 1, "result");
		if (r == s)
			nx_ko("returned s instead of a new string");
		else
			nx_free(r);
	}
	nx_end();
}

static void	strmapi_mf(long n)
{
	char	*r;

	nx_fail_at(n);
	nx_track(1);
	r = ft_strmapi("hello", map_upper);
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", nx_s(r));
		nx_free(r);
	}
}

void	t_strmapi(void)
{
	strmapi_case("hello", 1, "toupper (transform alpha)");
	strmapi_case("123 !?", 1, "toupper (non alpha)");
	strmapi_case("aaaaaa", 0, "f(i, c) = c + i (index passed)");
	strmapi_case("0000000000", 0, "f(i, c) = c + i");
	strmapi_case("", 0, "f(i, c) = c + i (empty string)");
	strmapi_case("hello", 1, "toupper");
	malloc_fail_series(nx_last_calls(), strmapi_mf,
		"ft_strmapi(\"hello\", toupper)");
}

static void	iter_record(unsigned int i, char *c)
{
	if (g_ncalls < 256)
		g_idx[g_ncalls] = i;
	if (c != g_iter_s + i)
		g_badptr = 1;
	g_ncalls++;
	*c = (char)(*c + i);
}

static void	iter_upper(unsigned int i, char *c)
{
	(void)i;
	g_ncalls++;
	if (*c >= 'a' && *c <= 'z')
		*c -= 32;
}

static void	striteri_case(const char *s, int upper, const char *label)
{
	char	buf[256];
	char	exp[256];
	size_t	i;

	if (!nx_case("ft_striteri(%s, %s)", nx_s(s), label))
		return ;
	strcpy(buf, s);
	for (i = 0; s[i]; i++)
		exp[i] = upper ? (s[i] >= 'a' && s[i] <= 'z' ? s[i] - 32 : s[i])
			: (char)(s[i] + i);
	exp[i] = 0;
	g_ncalls = 0;
	g_badptr = 0;
	g_iter_s = buf;
	ft_striteri(buf, upper ? iter_upper : iter_record);
	if (strcmp(buf, exp))
		nx_expect(nx_f("s = %s", nx_s(exp)), nx_f("s = %s", nx_s(buf)));
	if (!upper)
	{
		check_indices(strlen(s));
		if (g_badptr)
			nx_ko("f must receive the address of s[i] (got another "
				"pointer)");
	}
	nx_end();
}

void	t_striteri(void)
{
	striteri_case("hello", 1, "toupper (transform alpha)");
	striteri_case("123 !?", 1, "toupper (non alpha)");
	striteri_case("aaaaaa", 0, "f(i, &c): c += i (index passed)");
	striteri_case("", 0, "f(i, &c): c += i (empty string, f not called)");
	striteri_case("0000000000", 0, "f(i, &c): c += i");
}

/* ---------------------------------------------------------------------- */
/* put*_fd                                                                 */
/* ---------------------------------------------------------------------- */

typedef void	(*t_emit)(int fd, const void *arg);

static void	e_char(int fd, const void *a) { ft_putchar_fd(*(const char *)a, fd); }
static void	e_str(int fd, const void *a) { ft_putstr_fd((char *)a, fd); }
static void	e_endl(int fd, const void *a) { ft_putendl_fd((char *)a, fd); }
static void	e_nbr(int fd, const void *a) { ft_putnbr_fd(*(const int *)a, fd); }

/* target: -1 = pipe fd passed directly, 1 / 2 = redirect that fd */
static void	put_case(const char *call, t_emit fn, const void *arg,
				const char *exp, size_t elen, int target)
{
	int		fds[2];
	char	*got;
	size_t	len;
	ssize_t	r;

	if (!nx_case("%s", call))
		return ;
	if (pipe(fds) < 0)
	{
		nx_ko("pipe failed");
		nx_end();
		return ;
	}
	if (target > 0)
	{
		dup2(fds[1], target);
		fn(target, arg);
		close(target);
	}
	else
		fn(fds[1], arg);
	close(fds[1]);
	got = nx_raw_malloc(elen + 4096);
	len = 0;
	while ((r = read(fds[0], got + len, elen + 4096 - len)) > 0)
		len += r;
	close(fds[0]);
	if (len != elen || memcmp(got, exp, elen))
		nx_expect(nx_f("bytes written: %s", nx_m(exp, elen)),
			nx_f("bytes written: %s", nx_m(got, len)));
	nx_raw_free(got);
	nx_end();
}

static void	put_badfd(const char *call, t_emit fn, const void *arg)
{
	if (!nx_case("%s (invalid fd: no crash)", call))
		return ;
	fn(-1, arg);
	fn(1000, arg);
	nx_end();
}

static void	putchar_one(char c, int target)
{
	put_case(nx_f("ft_putchar_fd(%s, %s)", nx_ch(c), target == 1 ? "1"
			: target == 2 ? "2" : "fd"), e_char, &c, &c, 1, target);
}

void	t_putchar_fd(void)
{
	char	c;

	putchar_one('a', -1);
	putchar_one('a', 1);
	putchar_one('z', 2);
	putchar_one('\n', -1);
	putchar_one('\0', -1);
	putchar_one((char)0xe9, -1);
	putchar_one((char)0x80, -1);
	c = 'x';
	put_badfd("ft_putchar_fd('x', -1)", e_char, &c);
}

static void	str_one(int endl, const char *s, const char *label, int target)
{
	char	*exp;
	size_t	n;

	n = strlen(s);
	exp = nx_raw_malloc(n + 2);
	memcpy(exp, s, n);
	exp[n] = '\n';
	put_case(nx_f("ft_put%s_fd(%s, %s)", endl ? "endl" : "str",
			label ? label : nx_s(s), target == 1 ? "1" : target == 2 ? "2"
			: "fd"), endl ? e_endl : e_str, s, exp, n + endl, target);
	nx_raw_free(exp);
}

static void	str_all(int endl)
{
	static char	big[20001];
	char		*s;

	str_one(endl, "hello", NULL, -1);
	str_one(endl, "hello", NULL, 1);
	str_one(endl, "hello", NULL, 2);
	str_one(endl, "", NULL, -1);
	str_one(endl, "a\nb\tc", NULL, -1);
	str_one(endl, "\xe9t\xe9\x80", NULL, -1);
	memset(big, 'm', 20000);
	str_one(endl, big, "<20000 x 'm'>", -1);
	s = "abc";
	put_badfd(nx_f("ft_put%s_fd(\"abc\", -1)", endl ? "endl" : "str"),
		endl ? e_endl : e_str, s);
}

void	t_putstr_fd(void)
{
	str_all(0);
}

void	t_putendl_fd(void)
{
	str_all(1);
}

static void	nbr_one(int n, int target)
{
	char	exp[32];

	snprintf(exp, sizeof(exp), "%d", n);
	put_case(nx_f("ft_putnbr_fd(%s, %s)", n == INT_MIN ? "INT_MIN" : n
			== INT_MAX ? "INT_MAX" : nx_f("%d", n), target == 1 ? "1"
			: target == 2 ? "2" : "fd"), e_nbr, &n, exp, strlen(exp),
		target);
}

void	t_putnbr_fd(void)
{
	static const int	v[] = {0, 1, -1, 9, 10, -10, 42, -42, 100, 101,
		123456789, -987654321, 2147483646, -2147483647, INT_MAX, INT_MIN};
	size_t				i;
	int					n;

	for (i = 0; i < sizeof(v) / sizeof(*v); i++)
		nbr_one(v[i], -1);
	nbr_one(42, 1);
	nbr_one(-42, 2);
	n = 42;
	put_badfd("ft_putnbr_fd(42, -1)", e_nbr, &n);
	(void)errno;
}
