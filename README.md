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

## Results

![KO](https://img.shields.io/badge/KO-f87171?style=flat-square) the subject requires it and it is broken<br>
![WARN](https://img.shields.io/badge/WARN-fbbf24?style=flat-square) up to your evaluator, or not written in the subject<br>
![SKIP](https://img.shields.io/badge/SKIP-7088b0?style=flat-square) a tool is missing, or `42next quick` was used<br>
![OK](https://img.shields.io/badge/OK-4ade80?style=flat-square) passed

Every check comes from the subject. A failure shows the exact call, the
expected output and yours. Your folder is never modified.

## Projects

<details>
<summary><b>libft</b></summary>

<br>

- every function vs libc or a reference: edge cases, `\0`, bytes > 127, `INT_MIN`
- malloc failing at each call: `NULL` returned, nothing leaked
- crashes and timeouts caught per case
- leaks per function (valgrind)
- `files`, `norm`, `makefile`, forbidden functions, README

`42next ft_split` tests one function.

</details>

<details>
<summary><b>ft_printf</b></summary>

<br>

- every conversion vs the real `printf`: bytes and return value
- `NULL` strings and pointers, `INT_MIN`, `UINT_MAX`, `%c` with `\0`
- bonus flags and width when `make bonus` exists
- leaks, norm, Makefile, forbidden functions, README

`42next p` tests one conversion.

</details>

<details>
<summary><b>get_next_line</b></summary>

<br>

- `BUFFER_SIZE` from 1 to 10000000, and none
- empty files, no final newline, very long lines, stdin
- invalid fd, `read` failing midway
- leaks and open fds, bonus with several fds

</details>

<details>
<summary><b>push_swap</b></summary>

<br>

- every error case: `Error` on stderr, nothing on stdout
- every permutation of 2 to 5 numbers, compared to the fewest moves possible
- 100 and 500 numbers: operations vs the subject's thresholds
- disorder levels, `--simple` / `--medium` / `--complex` / `--adaptive`, `--bench`
- leaks, norm, Makefile

</details>

<details>
<summary><b>Born2beRoot</b></summary>

<br>

- `signature.txt` vs the sha1 of the VM disk
- `check.sh` to run inside the VM: LVM, SSH, UFW, password policy, sudo, `monitoring.sh`

</details>

<details>
<summary><b>Python</b></summary>

<br>

Growing Code, Garden Guardian, Data Quest, Code Cultivation, Code Nexus, The Codex, Data Archivist.

- output compared line by line, blank lines included
- functions and classes called directly: return values, exceptions
- authorized functions, type hints, flake8, mypy

`42next ex3` tests one exercise.

</details>

## Resources

- [42-cli](https://github.com/brunogo25/42-cli), [francinette](https://github.com/xicodomingues/francinette), [Tripouille](https://github.com/Tripouille)
- For exams: [42exam.net](https://42exam.net/)
- Made by mlobry, with [Claude](https://claude.com/claude-code). A ⭐ helps others find it.
