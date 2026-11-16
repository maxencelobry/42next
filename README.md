# 42next

[![stars](https://img.shields.io/github/stars/maxencelobry/42next?style=flat-square&color=4f9dff&labelColor=0a1628)](https://github.com/maxencelobry/42next/stargazers)
[![license](https://img.shields.io/badge/license-MIT-4f9dff?style=flat-square&labelColor=0a1628)](LICENSE)
[![node](https://img.shields.io/badge/node-18%2B-4f9dff?style=flat-square&labelColor=0a1628)](https://nodejs.org/en/download)
[![python](https://img.shields.io/badge/python-3.9%2B-4f9dff?style=flat-square&labelColor=0a1628)](https://www.python.org/downloads/)

A tester for 42 projects. It finds your project and shows exactly what is wrong.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/maxencelobry/42next/main/install.sh | bash
```

Needs `node` 18+, `python3` and `git`.

<details>
<summary><b>Windows</b></summary>

<br>

For C projects, install [WSL](https://learn.microsoft.com/en-us/windows/wsl/install), open Ubuntu and run the command above.

For Python projects only, in PowerShell:

```powershell
git clone https://github.com/maxencelobry/42next.git $env:LOCALAPPDATA\42next
cd $env:LOCALAPPDATA\42next
npm install -g .
```

</details>

<details>
<summary><b>Optional tools</b></summary>

<br>

A check that needs a missing tool is skipped, and the end of the run shows
the command that installs it on your machine. On Ubuntu:

```sh
sudo apt install build-essential valgrind pipx      # cc, make, valgrind
pipx install norminette flake8 mypy
```

Uninstall:

```sh
rm -rf ~/.local/share/42next ~/.local/bin/42next ~/.cache/42next-*
```

</details>

## Usage

```sh
42next            # menu
42next all        # test everything
42next ft_split   # test one function (or ex3, a file...)
42next clean      # delete __pycache__, .mypy_cache, .o...
```

Run it in your project folder, in `ex3/`, or in a parent folder.

<details>
<summary><b>All commands</b></summary>

<br>

| Command | Does |
| --- | --- |
| `42next` | menu for the project around the current folder |
| `42next <folder>` | menu for that folder |
| `42next all` | test everything, no menu |
| `42next quick` | same, skip the slow checks (valgrind, big benchmarks, mypy) |
| `42next ft_split ex3 p` | test only these parts |
| `42next ft_split.c ft_strjoin.c` | test the parts of these files (or `ex3/main.py`) |
| `42next ft_split --verbose` | options go to the tester: `--verbose`, `--keep`, `--quick`, `--no-color` |
| `42next list` | list what can be tested |
| `42next watch ft_split` | test again each time you save a file |
| `42next commit` | test the last git commit only: what is graded |
| `42next clean` | delete `__pycache__`, `.mypy_cache`, `.o`, `a.out`... in the project |
| `42next clean-pc` | free disk space: caches, old versions, trash |
| `42next update` | get the latest tests now (also automatic, once a day) |
| `42next help` | show this list |

Menu keys: `↑↓` move, `enter` or `→` select, `1-9` jump, `space` tick, `q` or `←` back.

After a run: `r` run again, `f` only the failed parts, `c` choose other parts,
`w` watch (run again on save), `k` keep the build folder. `ctrl-c` stops a
test and keeps the menu. The last run is at the top of the menu.

</details>

## Projects

| Project | What is tested |
| --- | --- |
| libft | every function vs libc, edge cases, malloc failure, leaks, crashes |
| ft_printf | every conversion vs the real `printf`, bytes and return value, flags as warnings |
| get_next_line | `BUFFER_SIZE` 1 to 10000000, stdin, invalid fd, read errors, leaks, several fds as warnings |
| push_swap | errors, 2 to 5 numbers vs the fewest moves, 100 / 500 benchmarks, `--bench` |
| Born2beRoot | `signature.txt`, and `check.sh` to run inside the VM |
| NetPractice | the 10 exports of "Get my config" at the root, every IP, mask and route, the README |
| Inception | docker-compose.yml, Dockerfiles, nginx TLS, restart, network, volumes, secrets, the docs, and the running stack when docker is up |
| Python | output line by line, authorized functions, type hints, flake8, mypy |
| A-Maze-ing | 30+ bad configs, every maze checked (walls, border, connectivity, 3x3 areas, shortest path, perfect, "42", seed), the menu, the `mazegen` package |
| Pacman | Makefile rules, lint, bad arguments, configs and highscores, the game played headless with pygame |

For the live Inception checks, start your stack with `make` first: 42next
only reads the running containers, it never starts or stops them.

Python covers Growing Code, Code Cultivation, Garden Guardian, Data Quest,
Data Archivist, Code Nexus, The Codex, DataDeck, FuncMage, The Matrix and
Cosmic Data. C projects also get norminette, the Makefile (rules, flags,
relink), forbidden functions and the README.

## Details

Every check comes from the subject. A failure shows the exact call, the
expected output and yours.

- `KO`: the subject requires it.
- `WARN`: up to your evaluator.
- `SKIP`: a tool is missing, or `42next quick` was used.

Your folder is never modified: everything runs in a temporary copy.

For exams, use [42exam.net](https://42exam.net/).

## Resources

- [42-cli](https://github.com/brunogo25/42-cli), [francinette](https://github.com/xicodomingues/francinette), [Tripouille](https://github.com/Tripouille)
- Wrong test or missing case? [Open an issue](https://github.com/maxencelobry/42next/issues) with the subject version and the output.
- Made by mlobry, with [Claude](https://claude.com/claude-code). A ⭐ helps others find it.
