/* Part 3 - linked lists. */
#include "tests.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int	g_vals[2000];
static void	*g_deleted[2000];
static int	g_ndel;
static int	g_niter;
static void	*g_iterated[2000];

static void	del_record(void *p)
{
	if (g_ndel < 2000)
		g_deleted[g_ndel] = p;
	g_ndel++;
}

static void	iter_record(void *p)
{
	if (g_niter < 2000)
		g_iterated[g_niter] = p;
	g_niter++;
}

/* list of n test nodes (tracked, not counted as library mallocs) */
static t_list	*build(int n)
{
	t_list	*head;
	t_list	*node;
	int		i;

	head = NULL;
	for (i = n - 1; i >= 0; i--)
	{
		node = nx_tmalloc(sizeof(t_list));
		g_vals[i] = i * 10;
		node->content = &g_vals[i];
		node->next = head;
		head = node;
	}
	return (head);
}

static void	free_list(t_list *l)
{
	t_list	*next;

	while (l)
	{
		next = l->next;
		nx_free(l);
		l = next;
	}
}

static const char	*list_repr(t_list *l, int max)
{
	char	buf[600];
	size_t	k;
	int		i;

	k = 0;
	for (i = 0; l && i < max && k < 500; i++, l = l->next)
	{
		if (l->content >= (void *)g_vals && l->content
			< (void *)(g_vals + 2000))
			k += snprintf(buf + k, sizeof(buf) - k, "[%d] -> ",
					*(int *)l->content);
		else
			k += snprintf(buf + k, sizeof(buf) - k, "[%p] -> ", l->content);
	}
	snprintf(buf + k, sizeof(buf) - k, l ? "..." : "NULL");
	return (nx_f("%s", buf));
}

/* ---------------------------------------------------------------------- */

static void	lstnew_case(void *content, const char *label)
{
	t_list	*n;

	if (!nx_case("ft_lstnew(%s)", label))
		return ;
	nx_track(1);
	n = ft_lstnew(content);
	nx_track(0);
	if (!n)
	{
		nx_expect("a new node", "NULL");
		nx_end();
		return ;
	}
	if (n->content != content)
		nx_expect(nx_f("node->content == %p", content),
			nx_f("node->content == %p", n->content));
	if (n->next != NULL)
		nx_expect("node->next == NULL", nx_f("node->next == %p", n->next));
	nx_size_check(n, sizeof(t_list), "node");
	nx_free(n);
	nx_end();
}

static void	lstnew_mf(long n)
{
	t_list	*r;
	int		x;

	nx_fail_at(n);
	nx_track(1);
	r = ft_lstnew(&x);
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", "a node");
		nx_free(r);
	}
}

void	t_lstnew(void)
{
	static int	v = 42;
	static char	s[] = "hello";

	lstnew_case(&v, "&(int){42}");
	lstnew_case(s, "\"hello\"");
	lstnew_case(NULL, "NULL (null content is fine)");
	lstnew_case(&v, "&(int){42}");
	malloc_fail_series(nx_last_calls(), lstnew_mf, "ft_lstnew(&x)");
}

void	t_lstadd_front(void)
{
	t_list	*l;
	t_list	*a;
	t_list	*b;

	if (nx_case("ft_lstadd_front(&lst, new) with lst == NULL"))
	{
		l = NULL;
		a = build(1);
		nx_track(1);
		ft_lstadd_front(&l, a);
		nx_track(0);
		if (l != a)
			nx_expect("lst == new", nx_f("lst == %p (new = %p)", (void *)l,
					(void *)a));
		else if (a->next)
			nx_expect("new->next == NULL", "new->next changed");
		free_list(a);
		nx_end();
	}
	if (nx_case("ft_lstadd_front(&lst, new) on [0] -> [10] -> [20]"))
	{
		l = build(3);
		b = l;
		a = nx_tmalloc(sizeof(t_list));
		g_vals[100] = 99;
		a->content = &g_vals[100];
		a->next = NULL;
		nx_track(1);
		ft_lstadd_front(&l, a);
		nx_track(0);
		if (l != a || a->next != b)
			nx_expect("[99] -> [0] -> [10] -> [20] -> NULL",
				list_repr(l, 6));
		free_list(l);
		nx_end();
	}
	if (nx_case("ft_lstadd_front x3 from an empty list"))
	{
		t_list	*n[3];
		int		i;

		l = NULL;
		for (i = 0; i < 3; i++)
		{
			n[i] = nx_tmalloc(sizeof(t_list));
			g_vals[200 + i] = i;
			n[i]->content = &g_vals[200 + i];
			n[i]->next = NULL;
			nx_track(1);
			ft_lstadd_front(&l, n[i]);
			nx_track(0);
		}
		if (l != n[2] || n[2]->next != n[1] || n[1]->next != n[0]
			|| n[0]->next)
			nx_expect("[2] -> [1] -> [0] -> NULL", list_repr(l, 6));
		free_list(l);
		nx_end();
	}
}

static void	lstsize_case(int n)
{
	t_list			*l;
	unsigned int	r;

	if (!nx_case("ft_lstsize(<list of %d node(s)>)%s", n,
			n ? "" : " (NULL)"))
		return ;
	l = build(n);
	nx_track(1);
	r = ft_lstsize(l);
	nx_track(0);
	if (r != (unsigned int)n)
		nx_expect(nx_f("%d", n), nx_f("%u", r));
	free_list(l);
	nx_end();
}

void	t_lstsize(void)
{
	lstsize_case(0);
	lstsize_case(1);
	lstsize_case(2);
	lstsize_case(5);
	lstsize_case(1000);
}

static void	lstlast_case(int n)
{
	t_list	*l;
	t_list	*exp;
	t_list	*r;

	if (!nx_case("ft_lstlast(<list of %d node(s)>)%s", n,
			n ? "" : " (NULL)"))
		return ;
	l = build(n);
	exp = l;
	while (exp && exp->next)
		exp = exp->next;
	nx_track(1);
	r = ft_lstlast(l);
	nx_track(0);
	if (r != exp)
		nx_expect(exp ? nx_f("node [%d]", *(int *)exp->content) : "NULL",
			r ? nx_f("%p", (void *)r) : "NULL");
	free_list(l);
	nx_end();
}

void	t_lstlast(void)
{
	lstlast_case(0);
	lstlast_case(1);
	lstlast_case(2);
	lstlast_case(5);
	lstlast_case(1000);
}

void	t_lstadd_back(void)
{
	t_list	*l;
	t_list	*a;
	t_list	*last;

	if (nx_case("ft_lstadd_back(&lst, new) with lst == NULL"))
	{
		l = NULL;
		a = build(1);
		nx_track(1);
		ft_lstadd_back(&l, a);
		nx_track(0);
		if (l != a)
			nx_expect("lst == new", nx_f("lst == %p (new = %p)", (void *)l,
					(void *)a));
		free_list(a);
		nx_end();
	}
	if (nx_case("ft_lstadd_back(&lst, new) on [0] -> [10] -> [20]"))
	{
		l = build(3);
		last = l->next->next;
		a = nx_tmalloc(sizeof(t_list));
		g_vals[100] = 99;
		a->content = &g_vals[100];
		a->next = NULL;
		nx_track(1);
		ft_lstadd_back(&l, a);
		nx_track(0);
		if (last->next != a || a->next != NULL)
			nx_expect("[0] -> [10] -> [20] -> [99] -> NULL",
				list_repr(l, 6));
		free_list(l);
		nx_end();
	}
	if (nx_case("ft_lstadd_back x3 from an empty list"))
	{
		t_list	*n[3];
		int		i;

		l = NULL;
		for (i = 0; i < 3; i++)
		{
			n[i] = nx_tmalloc(sizeof(t_list));
			g_vals[200 + i] = i;
			n[i]->content = &g_vals[200 + i];
			n[i]->next = NULL;
			nx_track(1);
			ft_lstadd_back(&l, n[i]);
			nx_track(0);
		}
		if (l != n[0] || n[0]->next != n[1] || n[1]->next != n[2]
			|| n[2]->next)
			nx_expect("[0] -> [1] -> [2] -> NULL", list_repr(l, 6));
		free_list(l);
		nx_end();
	}
	if (nx_case("ft_lstadd_back(&lst, <chain [0] -> [10]>) on [0]"))
	{
		l = build(1);
		a = build(2);
		nx_track(1);
		ft_lstadd_back(&l, a);
		nx_track(0);
		if (l->next != a || a->next == NULL)
			nx_expect("[0] -> [0] -> [10] -> NULL (chain kept)",
				list_repr(l, 6));
		free_list(l);
		nx_end();
	}
}

void	t_lstdelone(void)
{
	t_list	*l;
	t_list	*second;

	if (!nx_case("ft_lstdelone(<first node of [0] -> [10] -> [20]>, del)"))
		return ;
	l = build(3);
	second = l->next;
	g_ndel = 0;
	nx_track(1);
	ft_lstdelone(l, del_record);
	nx_track(0);
	if (g_ndel != 1)
		nx_ko("del called %d time(s), expected exactly once", g_ndel);
	else if (g_deleted[0] != &g_vals[0])
		nx_ko("del called on %p, expected the node content %p",
			g_deleted[0], (void *)&g_vals[0]);
	if (nx_is_live(l))
	{
		nx_ko("the node itself was not freed");
		nx_free(l);
	}
	if (!nx_is_live(second) || !nx_is_live(second->next))
	{
		nx_ko("freed the next node(s) too: lstdelone must free only one "
			"node");
		nx_no_leak_check();
	}
	else
		free_list(second);
	nx_end();
}

static void	lstclear_case(int n)
{
	t_list	*l;
	t_list	*nodes[20];
	t_list	*p;
	int		i;
	int		j;

	if (!nx_case("ft_lstclear(&lst, del) on a list of %d node(s)%s", n,
			n ? "" : " (lst == NULL)"))
		return ;
	l = build(n);
	for (i = 0, p = l; p && i < 20; p = p->next)
		nodes[i++] = p;
	g_ndel = 0;
	nx_track(1);
	ft_lstclear(&l, del_record);
	nx_track(0);
	if (l != NULL)
	{
		nx_expect("*lst == NULL after ft_lstclear",
			nx_f("*lst == %p (dangling)", (void *)l));
		nx_note("subject: \"Finally, set the pointer to the list to NULL\"");
	}
	if (g_ndel != n)
		nx_ko("del called %d time(s), expected %d (once per node)", g_ndel,
			n);
	for (i = 0; i < n; i++)
	{
		for (j = 0; j < g_ndel && j < 2000 && g_deleted[j] != &g_vals[i]; j++)
			;
		if (j == g_ndel || j == 2000)
		{
			nx_ko("del never called on the content of node %d", i);
			break ;
		}
	}
	for (i = 0; i < n && i < 20; i++)
		if (nx_is_live(nodes[i]))
		{
			nx_ko("node %d not freed", i);
			break ;
		}
	nx_end();
}

void	t_lstclear(void)
{
	lstclear_case(0);
	lstclear_case(1);
	lstclear_case(5);
	lstclear_case(20);
}

static void	lstiter_case(int n)
{
	t_list	*l;
	int		i;

	if (!nx_case("ft_lstiter(<list of %d node(s)>, f)%s", n,
			n ? "" : " (NULL)"))
		return ;
	l = build(n);
	g_niter = 0;
	nx_track(1);
	ft_lstiter(l, iter_record);
	nx_track(0);
	if (g_niter != n)
		nx_ko("f called %d time(s), expected %d", g_niter, n);
	for (i = 0; i < n && i < g_niter; i++)
		if (g_iterated[i] != &g_vals[i])
		{
			nx_ko("call %d of f got %p, expected the content of node %d "
				"(in list order)", i, g_iterated[i], i);
			break ;
		}
	free_list(l);
	nx_end();
}

void	t_lstiter(void)
{
	lstiter_case(0);
	lstiter_case(1);
	lstiter_case(5);
}

/* f for lstmap: content = new int (raw malloc, counted) = value + 1 */
static int	g_live_content;

static void	*map_inc(void *p)
{
	int	*r;

	r = nx_raw_malloc(sizeof(int));
	*r = *(int *)p + 1;
	g_live_content++;
	return (r);
}

static void	del_content(void *p)
{
	g_ndel++;
	if (p)
	{
		g_live_content--;
		nx_raw_free(p);
	}
}

static void	free_mapped(t_list *l)
{
	t_list	*next;

	while (l)
	{
		next = l->next;
		if (l->content)
		{
			g_live_content--;
			nx_raw_free(l->content);
		}
		nx_free(l);
		l = next;
	}
}

static void	lstmap_case(int n)
{
	t_list	*l;
	t_list	*r;
	t_list	*p;
	t_list	*q;
	int		i;

	if (!nx_case("ft_lstmap(<list [0] -> [10] ... of %d node(s)>, f = +1, "
			"del)", n))
		return ;
	l = build(n);
	g_ndel = 0;
	g_live_content = 0;
	nx_track(1);
	r = ft_lstmap(l, map_inc, del_content);
	nx_track(0);
	if (n == 0 && r)
		nx_expect("NULL", "a list");
	for (i = 0, p = r, q = l; i < n; i++, p = p->next, q = q->next)
	{
		if (!p)
		{
			nx_ko("new list has %d node(s), expected %d", i, n);
			break ;
		}
		if (p == q)
		{
			nx_ko("node %d of the new list is a node of the old list", i);
			break ;
		}
		if (!nx_is_live(p))
		{
			nx_ko("node %d of the new list was not allocated with malloc", i);
			break ;
		}
		if (!p->content || *(int *)p->content != i * 10 + 1)
		{
			nx_expect(nx_f("node %d content == %d", i, i * 10 + 1),
				p->content ? nx_f("%d", *(int *)p->content) : "NULL");
			break ;
		}
		if (*(int *)q->content != i * 10)
		{
			nx_ko("the original list was modified (node %d)", i);
			break ;
		}
	}
	if (!nx_failed() && p)
		nx_ko("new list is longer than %d node(s) (not NULL terminated?)", n);
	if (!nx_failed())
		free_mapped(r);
	else
		nx_no_leak_check();
	free_list(l);
	nx_end();
}

static void	lstmap_mf(long n)
{
	t_list	*l;
	t_list	*r;

	l = build(3);
	g_ndel = 0;
	g_live_content = 0;
	nx_fail_at(n);
	nx_track(1);
	r = ft_lstmap(l, map_inc, del_content);
	nx_track(0);
	if (r)
	{
		nx_expect("NULL", list_repr(r, 5));
		free_mapped(r);
	}
	else if (g_live_content > 0)
		nx_ko("%d content(s) returned by f never passed to del (leak)",
			g_live_content);
	if (*(int *)l->content != 0 || l->next == NULL
		|| !nx_is_live(l) || !nx_is_live(l->next))
	{
		nx_ko("the original list was modified or freed");
		nx_no_leak_check();
		return ;
	}
	free_list(l);
}

void	t_lstmap(void)
{
	lstmap_case(0);
	lstmap_case(1);
	lstmap_case(5);
	lstmap_case(3);
	malloc_fail_series(nx_last_calls(), lstmap_mf,
		"ft_lstmap(<3 nodes>, f, del)");
}
