# 42next

[![GitHub stars](https://img.shields.io/github/stars/maxencelobry/42next?style=flat)](https://github.com/maxencelobry/42next/stargazers)

Zero-config tester for 42 projects. Run `42next` in your project folder: it finds the project, lets you test everything or one single function / exercise, and tells you exactly what is wrong: the call, the expected output, what you got, leaks, norm and Makefile problems.

```
 42next  ·  C
──────────────────────────────────────────────────────
 libft  ~/42/libft

 ▸ 1 Run everything
   2 Pick a part / a function     52 available
   3 Pick several
   4 Clean the project            __pycache__, .mypy_cache, .o, a.out...
   5 Back to projects
```

## Projects

| Project | What is checked |
|---|---|
| libft | every function vs libc or a reference, edge cases, exact allocation size, malloc failure, leaks, crashes, bonus lists |
| ft_printf | every conversion vs the real printf (bytes and return value), NULL cases, bonus flags if present |
| get_next_line | many BUFFER_SIZE values, empty / long / no-newline files, stdin, invalid fd, read errors, leaks and open fds, bonus multi-fd |
| push_swap | error handling, every permutation of 3 and 5, 100 / 500 benchmarks, strategies, leaks |
| Born2beRoot | `signature.txt` vs the VM disk sha1, README, plus `check.sh` to run inside the VM: LVM, SSH, UFW, password policy, sudo, `monitoring.sh` output... |
| Python piscine | Growing Code, Garden Guardian, Data Quest, Code Cultivation, Code Nexus, The Codex, Data Archivist: exact output line by line, authorized functions, type hints, flake8, mypy |

For C projects, every run also checks the norm, the Makefile (rules, `cc`, flags, no relink, clean / fclean / re), forbidden external functions, the README and files that should not be pushed.

Your project folder is never modified: everything is built and run in a temporary copy. No `__pycache__` or `.mypy_cache` is left behind.

## Install

Needs [Node.js](https://nodejs.org/en/download) 18+, [Python](https://www.python.org/downloads/) 3.9+ and [git](https://git-scm.com/downloads). No sudo, no config file.

### Linux / macOS

```sh
git clone https://github.com/maxencelobry/42next.git ~/42next
~/42next/install.sh
```

This links the `42next` command in `~/.local/bin`. 42next updates itself once a day with `git pull`, so you never have to reinstall.

### Windows

For C projects, use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) (Ubuntu) and follow the Linux steps inside it: `cc`, `make` and `valgrind` don't exist on plain Windows.

Python projects also work natively in PowerShell:

```powershell
git clone https://github.com/maxencelobry/42next.git $HOME\42next
cd $HOME\42next
npm install -g .
```

### Optional tools

Checks that need a missing tool are skipped and say so.

| Tool | Used for | Install |
|---|---|---|
| `cc`, `make` | C projects | Linux: `sudo apt install build-essential` · macOS: `xcode-select --install` |
| [norminette](https://github.com/42school/norminette) | norm check | `python3 -m pip install --user norminette` |
| [valgrind](https://valgrind.org/) | leaks, open fds (Linux only) | `sudo apt install valgrind` |
| [flake8](https://flake8.pycqa.org/) | Python style | `python3 -m pip install --user flake8` |
| [mypy](https://mypy-lang.org/) | Python types | `python3 -m pip install --user mypy` |

## Usage

```sh
42next                 # menu
42next all             # test everything, no menu
42next ft_split ex3    # test only these parts
42next ex2/ft_x.py     # test the exercise of that file
42next list            # list what can be tested
42next clean           # delete __pycache__, .mypy_cache, .o, a.out... in the project
42next clean-pc        # free disk space: caches, old versions, trash
```

It works from the project root, from inside an exercise folder (`ex3/`), from `src/`, or from a parent folder that contains the project.

Keys: `↑↓` move, `enter` select, `1-9` jump, `space` tick, `q` back, `r` run again after a test.

## Exams

For exam practice, use [42exam.net](https://42exam.net/).

## Support

If 42next saved you a retry, give it a ⭐ on [GitHub](https://github.com/maxencelobry/42next): it helps other students find it. Found a wrong test or a missing case? [Open an issue](https://github.com/maxencelobry/42next/issues) with the subject version and the output.

## Credits

Made by mlobry (42 Paris).

Inspired by [42-cli](https://github.com/brunogo25/42-cli) and [Tripouille's testers](https://github.com/Tripouille).

Thanks to [Claude](https://claude.com/claude-code) (Anthropic), who helped build the testers.
