/* 42next libft tester - harness implementation (see harness.h). */
#define _GNU_SOURCE
#include "harness.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

void		*__real_malloc(size_t n);
void		__real_free(void *p);
void		*__wrap_malloc(size_t n);
void		__wrap_free(void *p);

int			g_nx_nofork = 0;
int			g_nx_timeout = 5;

/* ---------------------------------------------------------------------- */
/* malloc tracking                                                         */
/* ---------------------------------------------------------------------- */

#define NX_MAXBLK 32768

typedef struct s_blk
{
	void	*p;
	size_t	n;
	long	seq;
}	t_blk;

static t_blk	g_blk[NX_MAXBLK];
static int		g_nblk;
static int		g_on;
static long		g_calls;
static long		g_failat;
static size_t	g_lastreq;
static long		g_last_calls;
static int		g_noleak;

static int	blk_find(const void *p)
{
	int	i;

	if (!p)
		return (-1);
	for (i = g_nblk - 1; i >= 0; i--)
		if (g_blk[i].p == p)
			return (i);
	return (-1);
}

static void	blk_add(void *p, size_t n, long seq)
{
	if (g_nblk >= NX_MAXBLK)
		return ;
	g_blk[g_nblk].p = p;
	g_blk[g_nblk].n = n;
	g_blk[g_nblk].seq = seq;
	g_nblk++;
}

static void	blk_del(int i)
{
	g_blk[i] = g_blk[g_nblk - 1];
	g_nblk--;
}

void	*__wrap_malloc(size_t n)
{
	void	*p;

	if (!g_on)
		return (__real_malloc(n));
	g_calls++;
	g_lastreq = n;
	if (g_failat && g_calls == g_failat)
		return (NULL);
	p = __real_malloc(n);
	if (p)
	{
		memset(p, 0xBE, n < (1 << 20) ? n : (1 << 20));
		blk_add(p, n, g_calls);
	}
	return (p);
}

void	__wrap_free(void *p)
{
	int	i;

	if (!p)
		return ;
	i = blk_find(p);
	if (i >= 0)
		blk_del(i);
	__real_free(p);
}

void	nx_track(int on) { g_on = on; }
void	nx_fail_at(long n) { g_failat = n; }
long	nx_malloc_calls(void) { return (g_calls); }
long	nx_last_calls(void) { return (g_last_calls); }
size_t	nx_last_request(void) { return (g_lastreq); }
int		nx_live_blocks(void) { return (g_nblk); }
void	nx_no_leak_check(void) { g_noleak = 1; }
void	*nx_raw_malloc(size_t n) { return (__real_malloc(n)); }
void	nx_raw_free(void *p) { __real_free(p); }

long	nx_block_size(const void *p)
{
	int	i;

	i = blk_find(p);
	return (i < 0 ? -1 : (long)g_blk[i].n);
}

int	nx_is_live(const void *p)
{
	return (blk_find(p) >= 0);
}

void	*nx_tmalloc(size_t n)
{
	void	*p;

	p = __real_malloc(n);
	if (p)
	{
		memset(p, 0xBE, n);
		blk_add(p, n, 0);
	}
	return (p);
}

void	nx_free(void *p)
{
	if (!p)
		return ;
	if (blk_find(p) < 0)
	{
		nx_ko("returned pointer is not a block from malloc (static, stack "
			"or middle of a block?): it cannot be freed");
		return ;
	}
	free(p);
}

int	nx_size_check(const void *p, size_t want, const char *what)
{
	long	got;

	if (!p)
		return (1);
	got = nx_block_size(p);
	if (got < 0)
	{
		nx_ko("%s: not the start of a block returned by malloc", what);
		return (0);
	}
	if ((size_t)got != want)
	{
		nx_info("%s: allocates %ld bytes, %zu are needed", what, got, want);
		return (0);
	}
	return (1);
}

static void	malloc_reset(void)
{
	g_nblk = 0;
	g_on = 0;
	g_calls = 0;
	g_failat = 0;
	g_lastreq = 0;
	g_noleak = 0;
}

/* ---------------------------------------------------------------------- */
/* string pool / formatting                                                */
/* ---------------------------------------------------------------------- */

#define NX_POOL 96
#define NX_POOLSZ 1200
#define NX_SHOW 200

static char	g_pool[NX_POOL][NX_POOLSZ];
static int	g_pooli;

static char	*pool(void)
{
	g_pooli = (g_pooli + 1) % NX_POOL;
	return (g_pool[g_pooli]);
}

const char	*nx_m(const void *ptr, size_t n)
{
	static const char	hex[] = "0123456789abcdef";
	const unsigned char	*p = ptr;
	char				*out;
	size_t				i;
	size_t				k;
	size_t				show;

	if (!ptr)
		return ("{{NULL}}");
	out = pool();
	show = n > NX_SHOW ? NX_SHOW : n;
	k = 0;
	out[k++] = '{';
	out[k++] = '{';
	for (i = 0; i < show; i++)
	{
		out[k++] = hex[p[i] >> 4];
		out[k++] = hex[p[i] & 15];
	}
	out[k++] = '}';
	out[k++] = '}';
	out[k] = 0;
	if (n > show)
		snprintf(out + k, NX_POOLSZ - k, " (+%zu more bytes)", n - show);
	return (out);
}

const char	*nx_s(const char *s)
{
	if (!s)
		return ("{{NULL}}");
	return (nx_m(s, strlen(s)));
}

const char	*nx_f(const char *fmt, ...)
{
	va_list	ap;
	char	*out;

	out = pool();
	va_start(ap, fmt);
	vsnprintf(out, NX_POOLSZ, fmt, ap);
	va_end(ap);
	return (out);
}

const char	*nx_ch(int c)
{
	unsigned char	u;

	u = (unsigned char)c;
	if (u == '\n')
		return ("'\\n'");
	if (u == '\t')
		return ("'\\t'");
	if (u == 0)
		return ("'\\0'");
	if (u == '\'')
		return ("'\\''");
	if (u == '\\')
		return ("'\\\\'");
	if (u >= 32 && u < 127)
		return (nx_f("'%c'", u));
	return (nx_f("'\\x%02x'", u));
}

const char	*nx_ptr(const void *p, const void *base)
{
	if (!p)
		return ("NULL");
	if (base && (const char *)p >= (const char *)base
		&& (const char *)p - (const char *)base < (1 << 24))
		return (nx_f("s + %td", (const char *)p - (const char *)base));
	return (nx_f("%p (outside s)", p));
}

/* ---------------------------------------------------------------------- */
/* reporting                                                               */
/* ---------------------------------------------------------------------- */

static int	g_out = -1;
static int	g_status;		/* 0 ok, 1 warn, 2 ko (child side) */
static char	g_call[2048];
static int	g_soft;
static int	g_func_timeouts;	/* cases of this function that timed out */

static void	put_line(char tag, const char *text)
{
	char	buf[4096];
	int		n;
	int		i;

	if (g_out < 0)
		return ;
	n = snprintf(buf, sizeof(buf) - 1, "%c\t%s", tag, text);
	if (n < 0)
		return ;
	if (n > (int)sizeof(buf) - 2)
		n = sizeof(buf) - 2;
	for (i = 2; i < n; i++)
		if (buf[i] == '\n' || buf[i] == '\r')
			buf[i] = ' ';
	buf[n++] = '\n';
	while (write(g_out, buf, n) < 0 && errno == EINTR)
		;
}

static void	vline(char tag, const char *fmt, va_list ap)
{
	char	buf[3000];

	vsnprintf(buf, sizeof(buf), fmt, ap);
	put_line(tag, buf);
}

void	nx_ko(const char *fmt, ...)
{
	va_list	ap;

	g_status = 2;
	put_line('S', "KO");
	va_start(ap, fmt);
	vline('D', fmt, ap);
	va_end(ap);
}

void	nx_warn(const char *fmt, ...)
{
	va_list	ap;

	if (g_status < 1)
		g_status = 1;
	put_line('S', "WARN");
	va_start(ap, fmt);
	vline('D', fmt, ap);
	va_end(ap);
}

void	nx_note(const char *fmt, ...)
{
	va_list	ap;

	va_start(ap, fmt);
	vline('D', fmt, ap);
	va_end(ap);
}

void	nx_info(const char *fmt, ...)
{
	va_list	ap;

	va_start(ap, fmt);
	vline('I', fmt, ap);
	va_end(ap);
}

void	nx_ctx(const char *fmt, ...)
{
	va_list	ap;

	va_start(ap, fmt);
	vline('X', fmt, ap);
	va_end(ap);
}

void	nx_expect(const char *exp, const char *got)
{
	g_status = 2;
	put_line('S', "KO");
	put_line('E', exp);
	put_line('G', got);
}

int	nx_failed(void)
{
	return (g_status == 2);
}

void	nx_func(const char *name)
{
	g_func_timeouts = 0;
	if (g_nx_nofork)
		return ;
	printf("@F\t%s\n", name);
	fflush(stdout);
}

/* ---------------------------------------------------------------------- */
/* case lifecycle                                                          */
/* ---------------------------------------------------------------------- */

static void	print_block(const char *status, const char *data)
{
	const char	*line;
	const char	*nl;
	const char	*ctx;
	size_t		ctxlen;

	printf("@C\t%s\t%s\n", status, g_call);
	ctx = NULL;
	ctxlen = 0;
	for (line = data; line && *line; line = nl ? nl + 1 : NULL)
	{
		nl = strchr(line, '\n');
		if (line[0] == 'X' && line[1] == '\t')
		{
			ctx = line + 2;
			ctxlen = nl ? (size_t)(nl - ctx) : strlen(ctx);
			continue ;
		}
		if (line[1] != '\t' || !strchr("EGDI", line[0]))
			continue ;
		printf("@%c\t%.*s\n", line[0], (int)(nl ? nl - line - 2
				: (long)strlen(line + 2)), line + 2);
	}
	if (ctx && (!strcmp(status, "CRASH") || !strcmp(status, "TIMEOUT")
			|| g_soft))
		printf("@D\twhile: %.*s\n", (int)ctxlen, ctx);
}

static int	vcase(const char *fmt, va_list ap, int soft)
{
	int		fds[2];
	pid_t	pid;
	char	*data;
	size_t	len;
	size_t	cap;
	ssize_t	r;
	int		wst;
	int		devnull;
	const char	*status;
	const char	*line;
	int			got_m;

	vsnprintf(g_call, sizeof(g_call), fmt, ap);
	g_soft = soft;
	if (g_nx_nofork)
	{
		if (soft)
			return (0);
		malloc_reset();
		g_status = 0;
		return (1);
	}
	/* two timeouts already: the function loops, do not wait for each case */
	if (g_func_timeouts >= 2)
	{
		printf("@C\tSKIP\t%s\n", g_call);
		fflush(stdout);
		return (0);
	}
	fflush(stdout);
	if (pipe(fds) < 0)
	{
		perror("pipe");
		exit(2);
	}
	pid = fork();
	if (pid < 0)
	{
		perror("fork");
		exit(2);
	}
	if (pid == 0)
	{
		close(fds[0]);
		g_out = fds[1];
		devnull = open("/dev/null", O_RDWR);
		dup2(devnull, 0);
		dup2(devnull, 1);
		dup2(devnull, 2);
		if (devnull > 2)
			close(devnull);
		malloc_reset();
		g_status = 0;
		alarm(g_nx_timeout);
		return (1);
	}
	close(fds[1]);
	cap = 4096;
	len = 0;
	data = __real_malloc(cap);
	while (data)
	{
		if (len + 2048 > cap)
		{
			char	*bigger;

			if (cap > (16u << 20))
				break ;
			bigger = __real_malloc(cap * 2);
			if (!bigger)
				break ;
			memcpy(bigger, data, len);
			__real_free(data);
			data = bigger;
			cap *= 2;
		}
		r = read(fds[0], data + len, cap - len - 1);
		if (r < 0 && errno == EINTR)
			continue ;
		if (r <= 0)
			break ;
		len += r;
	}
	close(fds[0]);
	while (waitpid(pid, &wst, 0) < 0 && errno == EINTR)
		;
	if (!data)
		return (0);
	data[len] = 0;
	g_last_calls = 0;
	got_m = 0;
	status = "OK";
	for (line = data; line && *line; line = strchr(line, '\n'),
		line = line ? line + 1 : NULL)
	{
		if (!strncmp(line, "S\tKO", 4))
			status = "KO";
		else if (!strncmp(line, "S\tWARN", 6) && !strcmp(status, "OK"))
			status = "WARN";
		else if (!strncmp(line, "M\t", 2))
		{
			g_last_calls = atol(line + 2);
			got_m = 1;
		}
	}
	if (WIFSIGNALED(wst))
		status = WTERMSIG(wst) == SIGALRM ? "TIMEOUT" : "CRASH";
	else if (WIFEXITED(wst) && (WEXITSTATUS(wst) != 0 || !got_m))
		status = "KO";
	if (WIFSIGNALED(wst) && WTERMSIG(wst) == SIGALRM)
		g_func_timeouts++;
	if (soft && strcmp(status, "OK"))
	{
		print_block("WARN", data);
		printf("@D\t(undefined behaviour for the libc, but some graders "
			"test it)\n");
	}
	else
		print_block(status, data);
	if (WIFSIGNALED(wst))
	{
		if (WTERMSIG(wst) == SIGALRM)
			printf("@D\tno result after %d s (infinite loop?)\n",
				g_nx_timeout);
		else
			printf("@D\tcrashed with %s\n", sigabbrev_np(WTERMSIG(wst))
				? nx_f("SIG%s", sigabbrev_np(WTERMSIG(wst)))
				: nx_f("signal %d", WTERMSIG(wst)));
	}
	else if (WIFEXITED(wst) && WEXITSTATUS(wst) != 0)
		printf("@D\tthe test process exited with code %d (exit() called "
			"by the library?)\n", WEXITSTATUS(wst));
	else if (WIFEXITED(wst) && !got_m)
		printf("@D\tthe function exited the program (exit(0) called by the "
			"library?): the case never reached its end\n");
	fflush(stdout);
	__real_free(data);
	return (0);
}

int	nx_case(const char *fmt, ...)
{
	va_list	ap;
	int		r;

	va_start(ap, fmt);
	r = vcase(fmt, ap, 0);
	va_end(ap);
	return (r);
}

int	nx_soft(const char *fmt, ...)
{
	va_list	ap;
	int		r;

	va_start(ap, fmt);
	r = vcase(fmt, ap, 1);
	va_end(ap);
	return (r);
}

static void	leak_check(void)
{
	size_t	bytes;
	int		i;
	int		n;

	n = g_nblk;
	if (g_noleak || n == 0)
		return ;
	bytes = 0;
	for (i = 0; i < n; i++)
		bytes += g_blk[i].n;
	nx_ko("leak: %d block(s), %zu byte(s) never freed", n, bytes);
	for (i = 0; i < n && i < 4; i++)
	{
		if (g_blk[i].seq)
			nx_note("  block from malloc #%ld, %zu bytes: %s",
				g_blk[i].seq, g_blk[i].n,
				nx_m(g_blk[i].p, g_blk[i].n > 24 ? 24 : g_blk[i].n));
		else
			nx_note("  test node / buffer of %zu bytes was not freed",
				g_blk[i].n);
	}
	if (n > 4)
		nx_note("  ... and %d more", n - 4);
}

void	nx_end(void)
{
	char	buf[64];

	g_on = 0;
	g_failat = 0;
	leak_check();
	g_last_calls = g_calls;
	snprintf(buf, sizeof(buf), "%ld", g_calls);
	put_line('M', buf);
	if (!g_nx_nofork)
		_exit(0);
	malloc_reset();
}
