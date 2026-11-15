/* 42next get_next_line runner. Linked with -Wl,--wrap=read so the test can
 * count bytes read and make read() fail on purpose.
 *
 * usage: runner MODE OUTFILE ARGS...   (see run.py)
 * output records: "L<len>:<bytes>\n" for a line, "N\n" for NULL,
 *                 "F<i>\n" (fd index, multi mode), "I<key>=<value>\n".
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>

char		*get_next_line(int fd);
ssize_t		__real_read(int fd, void *buf, size_t n);

/* ---- malloc tracking: blocks allocated inside get_next_line() ---- */
void		*__real_malloc(size_t n);
void		__real_free(void *p);
#define MAXBLK 65536

static void		*g_blk[MAXBLK];
static size_t	g_blksz[MAXBLK];
static int		g_nblk;
static int		g_on;

void	*__wrap_malloc(size_t n)
{
	void	*p;

	p = __real_malloc(n);
	if (g_on && p && g_nblk < MAXBLK)
	{
		g_blk[g_nblk] = p;
		g_blksz[g_nblk++] = n;
	}
	return (p);
}

void	__wrap_free(void *p)
{
	int	i;

	if (p)
	{
		for (i = g_nblk - 1; i >= 0; i--)
		{
			if (g_blk[i] == p)
			{
				g_blk[i] = g_blk[--g_nblk];
				g_blksz[i] = g_blksz[g_nblk];
				break ;
			}
		}
	}
	__real_free(p);
}

static char	*gnl(int fd)
{
	char	*line;

	g_on = 1;
	line = get_next_line(fd);
	g_on = 0;
	return (line);
}

static int	g_fail_fd = -1000;
static long	g_reads;
static long	g_bytes;
static FILE	*g_out;

ssize_t	__wrap_read(int fd, void *buf, size_t n)
{
	ssize_t	r;

	g_reads++;
	if (fd == g_fail_fd)
	{
		errno = EIO;
		return (-1);
	}
	r = __real_read(fd, buf, n);
	if (r > 0)
		g_bytes += r;
	return (r);
}

static void	rec(char *line)
{
	size_t	n;

	if (!line)
		fputs("N\n", g_out);
	else
	{
		n = strlen(line);
		fprintf(g_out, "L%zu:", n);
		fwrite(line, 1, n, g_out);
		fputc('\n', g_out);
	}
	fflush(g_out);
}

/* record one call, free the line, return 1 if a line was returned */
static int	call(int fd)
{
	char	*line;

	line = gnl(fd);
	rec(line);
	if (!line)
		return (0);
	free(line);
	return (1);
}

static void	read_all(int fd, long max)
{
	long	i;

	i = 0;
	while (i++ < max && call(fd))
		;
	call(fd);
	call(fd);
}

static char	*slurp(const char *path, size_t *len)
{
	int		fd;
	size_t	cap = 1 << 16;
	ssize_t	r;
	char	*buf;

	*len = 0;
	fd = open(path, O_RDONLY);
	buf = malloc(cap);
	while (fd >= 0 && buf && (r = __real_read(fd, buf + *len, cap - *len)) > 0)
	{
		*len += r;
		if (*len == cap)
			buf = realloc(buf, cap *= 2);
	}
	if (fd >= 0)
		close(fd);
	return (buf);
}

static int	open_or_die(const char *path)
{
	int	fd;

	fd = open(path, O_RDONLY);
	if (fd < 0)
	{
		perror(path);
		exit(2);
	}
	return (fd);
}

int	main(int ac, char **av)
{
	const char	*mode;
	int			fd;

	if (ac < 3)
		return (2);
	mode = av[1];
	g_out = fopen(av[2], "w");
	if (!g_out)
		return (2);
	if (!strcmp(mode, "read") && ac == 5)
	{
		fd = open_or_die(av[3]);
		read_all(fd, atol(av[4]));
		close(fd);
	}
	else if (!strcmp(mode, "fd0") && ac == 4)
		read_all(0, atol(av[3]));
	else if (!strcmp(mode, "stdinfile") && ac == 5)
	{
		fd = open_or_die(av[3]);
		dup2(fd, 0);
		close(fd);
		read_all(0, atol(av[4]));
	}
	else if (!strcmp(mode, "slowpipe") && ac == 5)
	{
		int		p[2];
		size_t	len, off;
		char	*data = slurp(av[3], &len);
		pid_t	pid;

		if (pipe(p) < 0)
			return (2);
		pid = fork();
		if (pid == 0)
		{
			close(p[0]);
			fclose(g_out);
			for (off = 0; off < len; off += 7)
			{
				if (write(p[1], data + off, len - off < 7 ? len - off : 7) < 0)
					_exit(1);
				usleep(300);
			}
			free(data);
			close(p[1]);
			_exit(0);
		}
		free(data);
		close(p[1]);
		read_all(p[0], atol(av[4]));
		close(p[0]);
		waitpid(pid, NULL, 0);
	}
	else if (!strcmp(mode, "badfd") && ac == 4)
	{
		int	tmp;

		call(-1);
		close(42);
		call(42);
		tmp = open(av[3], O_RDONLY);
		close(tmp);
		call(tmp);
		tmp = open("/dev/null", O_WRONLY);
		call(tmp);
		close(tmp);
		tmp = open(".", O_RDONLY | O_DIRECTORY);
		call(tmp);
		close(tmp);
		fd = open_or_die(av[3]);
		call(fd);
		close(fd);
	}
	else if (!strcmp(mode, "readerr") && ac == 6)
	{
		long	k = atol(av[4]);
		int		fd2;

		fd = open_or_die(av[3]);
		while (k-- > 0)
			call(fd);
		g_fail_fd = fd;
		call(fd);
		call(fd);
		g_fail_fd = -1000;
		fd2 = open_or_die(av[3]);
		close(fd);
		read_all(fd2, atol(av[5]));
		close(fd2);
	}
	else if (!strcmp(mode, "minread") && ac == 4)
	{
		char	*line;

		fd = open_or_die(av[3]);
		line = gnl(fd);
		fprintf(g_out, "Ibytes=%ld\nIreads=%ld\n", g_bytes, g_reads);
		rec(line);
		free(line);
		close(fd);
	}
	else if (!strcmp(mode, "multi") && ac >= 6)
	{
		long	max = atol(av[3]);
		int		badcalls = atoi(av[4]);
		int		n = ac - 5;
		int		fds[64];
		int		done[64];
		int		left = n;
		int		i;
		long	round = 0;

		for (i = 0; i < n && i < 64; i++)
		{
			fds[i] = open_or_die(av[5 + i]);
			done[i] = 0;
		}
		while (left > 0 && round++ < max)
		{
			for (i = 0; i < n; i++)
			{
				if (done[i])
					continue ;
				fprintf(g_out, "F%d\n", i);
				if (!call(fds[i]))
				{
					done[i] = 1;
					left--;
				}
			}
			if (badcalls)
			{
				fputs("F-1\n", g_out);
				call(-1);
			}
		}
		for (i = 0; i < n; i++)
			close(fds[i]);
	}
	else
	{
		fprintf(stderr, "runner: bad arguments\n");
		return (2);
	}
	{
		long	bytes = 0;
		int		k;

		for (k = 0; k < g_nblk; k++)
			bytes += (long)g_blksz[k];
		fprintf(g_out, "Ileak=%d:%ld\n", g_nblk, bytes);
	}
	fclose(g_out);
	return (0);
}
