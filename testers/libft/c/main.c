/* 42next libft tester - entry point.
 *   ./tester [--nofork] [--timeout S] ft_strlen ft_split ...
 * With no function name, every function is tested. */
#include "tests.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int	g_nx_timeout;

typedef struct s_entry
{
	const char	*name;
	void		(*test)(void);
	int			present;
}	t_entry;

#define PRESENT(f) ((void (*)(void))(f) != NULL)
#define ENTRY(n) {"ft_" #n, t_##n, 0},
#define FILLP(n) g_tab[i++].present = PRESENT(ft_##n);

static t_entry	g_tab[] = {TESTS(ENTRY) {NULL, NULL, 0}};

static void	run_one(t_entry *e)
{
	nx_func(e->name);
	if (!e->present)
	{
		if (!g_nx_nofork)
			printf("@N\t%s\n", e->name);
		fflush(stdout);
		return ;
	}
	e->test();
}

int	main(int argc, char **argv)
{
	int	i;
	int	j;
	int	any;

	i = 0;
	TESTS(FILLP)
	any = 0;
	for (i = 1; i < argc; i++)
	{
		if (!strcmp(argv[i], "--nofork"))
			g_nx_nofork = 1;
		else if (!strcmp(argv[i], "--timeout") && i + 1 < argc)
			g_nx_timeout = atoi(argv[++i]);
	}
	for (i = 1; i < argc; i++)
	{
		if (argv[i][0] == '-' || (i > 1 && !strcmp(argv[i - 1], "--timeout")))
			continue ;
		any = 1;
		for (j = 0; g_tab[j].name; j++)
			if (!strcmp(g_tab[j].name, argv[i]))
				run_one(&g_tab[j]);
	}
	if (!any)
		for (j = 0; g_tab[j].name; j++)
			run_one(&g_tab[j]);
	return (0);
}
