/* 42next libft tester - test harness.
 *
 * Every case runs in its own forked child (crash / timeout isolation).
 * The child reports through a pipe, the parent prints one protocol block
 * per case on stdout, which run.py turns into the report:
 *
 *   @F <function>                    start of a function
 *   @C <OK|KO|WARN|CRASH|TIMEOUT|SKIP> <call>
 *      (SKIP: not run, the function already timed out twice)
 *   @E <expected>   @G <got>   @D <detail line>
 *
 * Strings / byte buffers are sent as {{hex}} tokens ({{NULL}} for NULL);
 * run.py renders them with core.crepr.
 *
 * malloc / free are wrapped (-Wl,--wrap=malloc,--wrap=free) so a case can
 * see the exact size of each block, make the N-th malloc fail, and list
 * the blocks still alive at the end of the case (leaks).
 */
#ifndef HARNESS_H
# define HARNESS_H

# include <stddef.h>

int			nx_case(const char *fmt, ...)
			__attribute__((format(printf, 1, 2)));
int			nx_soft(const char *fmt, ...)
			__attribute__((format(printf, 1, 2)));
void		nx_end(void);
void		nx_func(const char *name);

void		nx_ko(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void		nx_warn(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void		nx_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void		nx_info(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void		nx_ctx(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void		nx_expect(const char *exp, const char *got);
int			nx_failed(void);

const char	*nx_s(const char *s);
const char	*nx_m(const void *p, size_t n);
const char	*nx_f(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
const char	*nx_ch(int c);
const char	*nx_ptr(const void *p, const void *base);

void		nx_track(int on);
void		nx_fail_at(long n);
long		nx_malloc_calls(void);
long		nx_last_calls(void);
size_t		nx_last_request(void);
long		nx_block_size(const void *p);
int			nx_is_live(const void *p);
int			nx_live_blocks(void);
void		nx_free(void *p);
void		*nx_tmalloc(size_t n);
void		*nx_raw_malloc(size_t n);
void		nx_raw_free(void *p);
void		nx_no_leak_check(void);
int			nx_size_check(const void *p, size_t want, const char *what);

extern int	g_nx_nofork;

#endif
