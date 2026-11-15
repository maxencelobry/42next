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
git clone https://github.com/maxencelobry/42next.git $HOME\42next
cd $HOME\42next
npm install -g .
```

</details>

<details>
<summary><b>Optional tools</b></summary>

<br>

A check that needs a missing tool is skipped and says so.

```sh
sudo apt install build-essential valgrind           # cc, make, valgrind
python3 -m pip install --user norminette flake8 mypy
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
| `42next quick` | same, without valgrind and long benchmarks |
| `42next ft_split ex3 p` | test only these parts |
| `42next ft_split.c` | test the part of that file |
| `42next list` | list what can be tested |
| `42next clean` | delete `__pycache__`, `.mypy_cache`, `.o`, `a.out`... in the project |
| `42next clean-pc` | free disk space: caches, old versions, trash |
| `42next update` | get the latest tests now (also automatic, once a day) |
| `42next help` | show this list |

Menu keys: `↑↓` move, `enter` select, `1-9` jump, `space` tick, `q` back, `r` run again.

</details>

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

## Resources

- [42-cli](https://github.com/brunogo25/42-cli), [francinette](https://github.com/xicodomingues/francinette), [Tripouille](https://github.com/Tripouille)
- For exams: [42exam.net](https://42exam.net/)
- Made by mlobry, with [Claude](https://claude.com/claude-code). A ⭐ helps others find it.
