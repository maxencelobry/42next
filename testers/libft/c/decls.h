/* Reference prototypes (libft subject v1.3). Weak, so a missing function
 * links as NULL instead of breaking the whole tester. The student's
 * libft.h is never included here: its prototypes are checked separately. */
#ifndef DECLS_H
# define DECLS_H

# include <stddef.h>

typedef struct s_list
{
	void			*content;
	struct s_list	*next;
}	t_list;

# define W __attribute__((weak))

int				ft_isalpha(int c) W;
int				ft_isdigit(int c) W;
int				ft_isalnum(int c) W;
int				ft_isascii(int c) W;
int				ft_isprint(int c) W;
size_t			ft_strlen(const char *s) W;
void			*ft_memset(void *s, int c, size_t n) W;
void			ft_bzero(void *s, size_t n) W;
void			*ft_memcpy(void *dst, const void *src, size_t n) W;
void			*ft_memmove(void *dst, const void *src, size_t n) W;
size_t			ft_strlcpy(char *dst, const char *src, size_t size) W;
size_t			ft_strlcat(char *dst, const char *src, size_t size) W;
int				ft_toupper(int c) W;
int				ft_tolower(int c) W;
char			*ft_strchr(const char *s, int c) W;
char			*ft_strrchr(const char *s, int c) W;
int				ft_strncmp(const char *s1, const char *s2, size_t n) W;
void			*ft_memchr(const void *s, int c, size_t n) W;
int				ft_memcmp(const void *s1, const void *s2, size_t n) W;
char			*ft_strnstr(const char *big, const char *little,
					size_t len) W;
int				ft_atoi(const char *nptr) W;
void			*ft_calloc(size_t nmemb, size_t size) W;
char			*ft_strdup(const char *s) W;
char			*ft_substr(char const *s, unsigned int start, size_t len) W;
char			*ft_strjoin(char const *s1, char const *s2) W;
char			*ft_strtrim(char const *s1, char const *set) W;
char			**ft_split(char const *s, char c) W;
char			*ft_itoa(int n) W;
char			*ft_strmapi(char const *s, char (*f)(unsigned int, char)) W;
void			ft_striteri(char *s, void (*f)(unsigned int, char *)) W;
void			ft_putchar_fd(char c, int fd) W;
void			ft_putstr_fd(char *s, int fd) W;
void			ft_putendl_fd(char *s, int fd) W;
void			ft_putnbr_fd(int n, int fd) W;
t_list			*ft_lstnew(void *content) W;
void			ft_lstadd_front(t_list **lst, t_list *new) W;
unsigned int	ft_lstsize(t_list *lst) W;
t_list			*ft_lstlast(t_list *lst) W;
void			ft_lstadd_back(t_list **lst, t_list *new) W;
void			ft_lstdelone(t_list *lst, void (*del)(void *)) W;
void			ft_lstclear(t_list **lst, void (*del)(void *)) W;
void			ft_lstiter(t_list *lst, void (*f)(void *)) W;
t_list			*ft_lstmap(t_list *lst, void *(*f)(void *),
					void (*del)(void *)) W;

# undef W

#endif
