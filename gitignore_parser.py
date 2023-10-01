import collections
import os
import re

from os.path import dirname
from pathlib import Path
from functools import partial
from typing import Reversible, Union, Optional, Callable

def assert_absolute(path: Path):
    if not path.is_absolute():
        raise ValueError('path must be absolute')

def str_lcut(s: str, match: str):
    if s.startswith(match):
        return s[len(match):], True
    else:
        return s, False

def str_rcut(s: str, match: str):
    if s.endswith(match):
        return s[:-len(match)], True
    else:
        return s, False

def handle_negation(rules: Reversible["IgnoreRule"], _file_path: Union[str, Path]):
    if isinstance(_file_path, Path):
        file_path = _file_path
        is_dir = None
    elif isinstance(_file_path, str):
        file_path = Path(_file_path)
        is_dir = _file_path[-1] == os.sep
    else:
        raise ValueError('path must be string or Path object')
    
    for rule in reversed(rules):
        if rule.match(file_path, is_dir):
            return not rule.negation
    return False

def parse_gitignore(
    _full_path: Union[str, Path],
    _base_dir: Optional[Union[str, Path]] = None
) -> Callable[[Union[str, Path]], bool]:
    full_path = Path(_full_path)
    if _base_dir is None: base_dir = full_path.parent
    else: base_dir = Path(_base_dir)
    
    rules: list[IgnoreRule] = []
    with open(full_path) as ignore_file:
        for lineno, line in enumerate(ignore_file):
            line = line.rstrip('\n')
            rule = rule_from_pattern(
                line,
                base_path = base_dir.resolve(),
                source = (full_path, lineno + 1)
            )
            if rule: rules.append(rule)
    
    return partial(handle_negation, rules)

def rule_from_pattern(pattern: str, base_path: Path, source: tuple[Path, int]):
    """
    Take a .gitignore match pattern, such as "*.py[cod]" or "**/*.bak",
    and return an IgnoreRule suitable for matching against files and
    directories. Patterns which do not match files, such as comments
    and blank lines, will return None.
    Because git allows for nested .gitignore files, a base_path value
    is required for correct behavior. The base path should be absolute.
    """
    assert_absolute(base_path)
    # Store the exact pattern for our repr and string functions
    orig_pattern = pattern
    # Early returns follow
    # Discard comments and separators
    if pattern.strip() == '' or pattern[0] == '#': return
    # Discard anything with more than two consecutive asterisks
    if pattern.find('***') > -1: return
    # Strip leading bang before examining double asterisks
    negation = pattern[0] == '!'
    if negation: pattern = pattern[1:]
    # Discard anything with invalid double-asterisks -- they can appear
    # at the start or the end, or be surrounded by slashes
    for m in re.finditer(r'\*\*', pattern):
        start_index = m.start()
        if (start_index != 0 and start_index != len(pattern) - 2 and
                (pattern[start_index - 1] != '/' or
                 pattern[start_index + 2] != '/')):
            return

    # Special-casing '/', which doesn't match any files or directories
    if pattern.rstrip() == '/': return

    directory_only = pattern[-1] == '/'
    # A slash is a sign that we're tied to the base_path of our rule set.
    anchored = '/' in pattern[:-1]
    pattern, _ = str_lcut(pattern, '/')
    pattern, have_double_asterisk = str_lcut(pattern, '**')
    if have_double_asterisk: anchored = False
    pattern, _ = str_lcut(pattern, '/')
    pattern, _ = str_rcut(pattern, '/')
    # patterns with leading hashes are escaped with a backslash in front, unescape it
    if pattern.startswith('\\#'): pattern = pattern[1:]
    # trailing spaces are ignored unless they are escaped with a backslash
    pattern = pattern.rstrip()
    pattern, have_escaped_space = str_rcut(pattern, '\\')
    if have_escaped_space: pattern += ' '
    regex = fnmatch_pathname_to_regex(
        pattern, directory_only, negation, anchored=bool(anchored)
    )
    return IgnoreRule(
        pattern=orig_pattern,
        regex=regex,
        negation=negation,
        directory_only=directory_only,
        anchored=anchored,
        base_path=base_path,
        source=source
    )

whitespace_re = re.compile(r'(\\ )+$')

IGNORE_RULE_FIELDS = [
    'pattern', 'regex',  # Basic values
    'negation', 'directory_only', 'anchored',  # Behavior flags
    'base_path',  # Meaningful for gitignore-style behavior
    'source'  # (file, line) tuple for reporting
]


class IgnoreRule(collections.namedtuple('IgnoreRule_', IGNORE_RULE_FIELDS)):
    pattern: str
    regex: str
    negation: bool
    directory_only: bool
    anchored: bool
    base_path: Path
    source: tuple[Path, int]
    
    def __str__(self):
        return self.pattern

    def __repr__(self):
        return 'IgnoreRule(\'%s\')' % self.pattern

    def match(self, abs_path: Path, is_dir: Optional[bool] = None):
        if is_dir is None: is_dir = abs_path.is_dir()
        try:
            rel_path_parts = abs_path.resolve().relative_to(self.base_path).parts
        except:
            return False
        rel_path = os.sep.join(rel_path_parts)
        if self.negation and is_dir: rel_path += '/'
        search_result = re.search(self.regex, rel_path)
        return bool(search_result)


# Frustratingly, python's fnmatch doesn't provide the FNM_PATHNAME
# option that .gitignore's behavior depends on.
def fnmatch_pathname_to_regex(
    pattern, directory_only: bool, negation: bool, anchored: bool = False
):
    """
    Implements fnmatch style-behavior, as though with FNM_PATHNAME flagged;
    the path separator will not match shell-style '*' and '.' wildcards.
    """
    i, n = 0, len(pattern)

    seps = [re.escape(os.sep)]
    if os.altsep is not None:
        seps.append(re.escape(os.altsep))
    seps_group = '[' + '|'.join(seps) + ']'
    nonsep = r'[^{}]'.format('|'.join(seps))

    res = []
    while i < n:
        c = pattern[i]
        i += 1
        if c == '*':
            try:
                if pattern[i] == '*':
                    i += 1
                    res.append('.*')
                    if pattern[i] == '/':
                        i += 1
                        res.append(''.join([seps_group, '?']))
                else:
                    res.append(''.join([nonsep, '*']))
            except IndexError:
                res.append(''.join([nonsep, '*']))
        elif c == '?':
            res.append(nonsep)
        elif c == '/':
            res.append(seps_group)
        elif c == '[':
            j = i
            if j < n and pattern[j] == '!':
                j += 1
            if j < n and pattern[j] == ']':
                j += 1
            while j < n and pattern[j] != ']':
                j += 1
            if j >= n:
                res.append('\\[')
            else:
                stuff = pattern[i:j].replace('\\', '\\\\')
                i = j + 1
                if stuff[0] == '!':
                    stuff = ''.join(['^', stuff[1:]])
                elif stuff[0] == '^':
                    stuff = ''.join('\\' + stuff)
                res.append('[{}]'.format(stuff))
        else:
            res.append(re.escape(c))
    if anchored:
        res.insert(0, '^')
    res.insert(0, '(?ms)')
    if not directory_only:
        res.append('$')
    elif directory_only and negation:
        res.append('/$')
    else:
        res.append('($|\/)')
    return ''.join(res)
