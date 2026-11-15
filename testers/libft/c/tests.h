#ifndef TESTS_H
# define TESTS_H

# include "harness.h"
# include "decls.h"

# define TESTS(X) \
	X(isalpha) X(isdigit) X(isalnum) X(isascii) X(isprint) X(strlen) \
	X(memset) X(bzero) X(memcpy) X(memmove) X(strlcpy) X(strlcat) \
	X(toupper) X(tolower) X(strchr) X(strrchr) X(strncmp) X(memchr) \
	X(memcmp) X(strnstr) X(atoi) X(calloc) X(strdup) \
	X(substr) X(strjoin) X(strtrim) X(split) X(itoa) X(strmapi) \
	X(striteri) X(putchar_fd) X(putstr_fd) X(putendl_fd) X(putnbr_fd) \
	X(lstnew) X(lstadd_front) X(lstsize) X(lstlast) X(lstadd_back) \
	X(lstdelone) X(lstclear) X(lstiter) X(lstmap)

# define DECL(n) void t_##n(void);
TESTS(DECL)
# undef DECL

/* shared helpers (t_part2.c) */
void	check_str_result(const char *got, const char *exp, const char *what);
void	malloc_fail_series(long calls, void (*fn)(long n), const char *call);

#endif
