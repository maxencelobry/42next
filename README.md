# 42next

A tester for 42 projects. It finds your project and shows exactly what is wrong.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/maxencelobry/42next/main/install.sh | bash
```

Needs `node` 18+, `python3` and `git`.

## Usage

```sh
42next            # menu
42next all        # test everything
42next ft_split   # test one function (or ex3, a file...)
42next clean      # delete __pycache__, .mypy_cache, .o...
```

Run it in your project folder, in `ex3/`, or in a parent folder.

## Projects

- libft
- ft_printf
- get_next_line
- push_swap
- Born2beRoot
- Python: Growing Code, Garden Guardian, Data Quest, Code Cultivation, Code Nexus, The Codex, Data Archivist

## Details

Every check comes from the subject. A failure shows the exact call, the
expected output and yours.

- `KO`: the subject requires it.
- `WARN`: up to your evaluator.
- `SKIP`: a tool is missing, or `42next quick` was used.

C projects also get norminette, the Makefile (rules, flags, relink), forbidden
functions, leaks and the README. Python projects get flake8, mypy and the
authorized functions. Your folder is never modified.

For exams, use [42exam.net](https://42exam.net/).

## Resources

- [42-cli](https://github.com/brunogo25/42-cli), [francinette](https://github.com/xicodomingues/francinette), [Tripouille](https://github.com/Tripouille)
- Made by mlobry, with [Claude](https://claude.com/claude-code). A ⭐ helps others find it.
