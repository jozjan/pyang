"""YANG output plugin"""

import optparse

from .. import plugin
from .. import util
from .. import grammar

def pyang_plugin_init():
    plugin.register_plugin(YANGPlugin())

class YANGPlugin(plugin.PyangPlugin):
    def add_output_format(self, fmts):
        fmts['yang'] = self
        self.handle_comments = True

    def add_opts(self, optparser):
        optlist = [
            optparse.make_option("--yang-canonical",
                                 dest="yang_canonical",
                                 action="store_true",
                                 help="Print in canonical order"),
            optparse.make_option("--yang-remove-unused-imports",
                                 dest="yang_remove_unused_imports",
                                 action="store_true"),
            optparse.make_option("--yang-line-length",
                                 type="int",
                                 dest="yang_line_length",
                                 help="Maximum line length"),
            ]
        g = optparser.add_option_group("YANG output specific options")
        g.add_options(optlist)

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False
        ctx.keep_arg_substrings = True

    def emit(self, ctx, modules, fd):
        module = modules[0]
        emit_yang(ctx, module, fd)

def emit_yang(ctx, module, fd):
    emit_stmt(ctx, module, fd, 0, None, '', '  ')

# always add newline between keyword and argument
_force_newline_arg = ('description', 'reference', 'contact', 'organization')

# do not quote these arguments
_non_quote_arg_type = ('identifier', 'identifier-ref', 'boolean', 'integer',
                       'non-negative-integer', 'max-value',
                       'date', 'ordered-by-arg',
                       'fraction-digits-arg', 'deviate-arg', 'version',
                       'status-arg')

_maybe_quote_arg_type = ('enum-arg', )

# add extra blank line after these, when they occur on the top level
_keyword_with_trailing_blank_line_toplevel = (
    'description',
    'identity',
    'feature',
    'extension',
    'rpc',
    'augment',
    'deviation',
    )

# always add extra blank line after these
_keyword_with_trailing_blank_line = (
    'typedef',
    'grouping',
    'notification',
    'action',
    )

# use single quote for the arguments to these keywords (if possible)
_keyword_prefer_single_quote_arg = (
    'must',
    'when',
    'pattern',
)

_keyword_with_path_arg = (
## FIXME: tmp
#    'augment',
#    'refine',
#    'deviation',
#    'path',
)

_kwd_class = {
    'yang-version': 'header',
    'namespace': 'header',
    'prefix': 'header',
    'belongs-to': 'header',
    'organization': 'meta',
    'contact': 'meta',
    'description': 'meta',
    'reference': 'meta',
    'import': 'linkage',
    'include': 'linkage',
    'revision': 'revision',
    'typedef': 'defs',
    'grouping': 'defs',
    'identity': 'defs',
    'feature': 'defs',
    'extension': 'defs',
    '_comment': 'comment',
    'module': None,
    'submodule': None,
}
def get_kwd_class(keyword):
    if util.is_prefixed(keyword):
        return 'extension'
    else:
        try:
            return _kwd_class[keyword]
        except KeyError:
            return 'body'

_need_quote = (
    " ", "}", "{", ";", '"', "'",
    "\n", "\t", "\r", "//", "/*", "*/",
    )

def emit_stmt(ctx, stmt, fd, level, prev_kwd_class, indent, indentstep):
    if ctx.opts.yang_remove_unused_imports and stmt.keyword == 'import':
        for p in stmt.parent.i_unused_prefixes:
            if stmt.parent.i_unused_prefixes[p] == stmt:
                return

    max_line_len = ctx.opts.yang_line_length
    if util.is_prefixed(stmt.raw_keyword):
        (prefix, identifier) = stmt.raw_keyword
        keywordstr = prefix + ':' + identifier
    else:
        keywordstr = stmt.keyword

    kwd_class = get_kwd_class(stmt.keyword)
    if ((level == 1 and
         kwd_class != prev_kwd_class and kwd_class != 'extension') or
        (level == 1 and stmt.keyword in
         _keyword_with_trailing_blank_line_toplevel) or
        stmt.keyword in _keyword_with_trailing_blank_line):
        fd.write('\n')

    if stmt.keyword == '_comment':
        emit_comment(stmt.arg, fd, indent)
        return

    fd.write(indent + keywordstr)
    col = len(indent) + len(keywordstr)
    arg_on_new_line = False
    if len(stmt.substmts) == 0:
        eol = ';'
    else:
        eol = ' {'
    if stmt.arg is not None:
        # line_len is length of line w/o arg
        line_len = col + 1 + 2 + len(eol)
        # 1 is space before arg, 2 is quotes
        if (stmt.keyword in _keyword_prefer_single_quote_arg and
            stmt.arg.find("'") == -1):
            # print with single quotes
            if len(stmt.arg_substrings) > 1:
                # the arg was already split into multiple lines, keep them
                emit_multi_str_arg(keywordstr, stmt.arg_substrings, fd, "'",
                                   indent, indentstep, max_line_len, line_len)
            elif not(need_new_line(keywordstr, max_line_len,
                                   line_len, stmt.arg)):
                # fits into a single line
                fd.write(" '" + stmt.arg + "'")
            else:
                # otherwise, print on new line, don't check line length
                # since we can't break the string into multiple lines
                fd.write('\n' + indent + indentstep)
                fd.write("'" + stmt.arg + "'")
                arg_on_new_line = True
        elif len(stmt.arg_substrings) > 1:
            # the arg was already split into multiple lines, keep them
            emit_multi_str_arg(keywordstr, stmt.arg_substrings, fd, '"',
                               indent, indentstep, max_line_len, line_len)
        elif '\n' in stmt.arg:
            # the arg string contains newlines; print it as double quoted
            arg_on_new_line = emit_arg(keywordstr, stmt, fd, indent, indentstep,
                                       max_line_len, line_len)
        elif stmt.keyword in _keyword_with_path_arg:
            arg_on_new_line = emit_path_arg(keywordstr, stmt.arg, fd,
                                            indent, max_line_len, line_len, eol)
        elif stmt.keyword in grammar.stmt_map:
            (arg_type, _subspec) = grammar.stmt_map[stmt.keyword]
            if (arg_type in _non_quote_arg_type or
                (arg_type in _maybe_quote_arg_type and
                 not need_quote(stmt.arg))):
                if not(need_new_line(keywordstr, max_line_len,
                                     line_len, stmt.arg)):
                    fd.write(' ' + stmt.arg)
                else:
                    fd.write('\n' + indent + indentstep + stmt.arg)
                    arg_on_new_line = True
            else:
                arg_on_new_line = emit_arg(keywordstr, stmt, fd,
                                           indent, indentstep,
                                           max_line_len, line_len)
        else:
            arg_on_new_line = emit_arg(keywordstr, stmt, fd, indent, indentstep,
                                       max_line_len, line_len)
    fd.write(eol + '\n')

    if len(stmt.substmts) > 0:
        if ctx.opts.yang_canonical:
            substmts = grammar.sort_canonical(stmt.keyword, stmt.substmts)
        else:
            substmts = stmt.substmts
        if level == 0:
            kwd_class = 'header'
        for s in substmts:
            n = 1
            if arg_on_new_line:
                # arg was printed on a new line, increase indentation
                n = 2
            emit_stmt(ctx, s, fd, level + 1, kwd_class,
                      indent + (indentstep * n), indentstep)
            kwd_class = get_kwd_class(s.keyword)
        fd.write(indent + '}\n')

def need_new_line(keywordstr, max_line_len, line_len, arg):
    if (max_line_len is not None and
        line_len + len(arg) > max_line_len and
        len(keywordstr) > 8):
        # if the keyword is short, we don't want to add the extra new line,
        # e.g., not:
        #    must
        #      'long line here'
        return True
    else:
        return False

def emit_multi_str_arg(keywordstr, strs, fd, pref_q,
                       indent, indentstep, max_line_len,
                       line_len):
    # we want to align all strings on the same column; check if
    # we can print w/o a newline
    need_new_line = False
    if (max_line_len is not None and len(keywordstr) > 6):
        for (s, q) in strs:
            q = select_quote(s, q, pref_q)
            if q == '"':
                s = escape_str(s)
            if line_len + len(s) > max_line_len:
                need_new_line = True
                break
    if need_new_line:
        fd.write('\n' + indent + indentstep)
        prefix = (len(indent) - 2) * ' ' + indentstep + '+ '
    else:
        fd.write(' ')
        prefix = indent + ((len(keywordstr) - 1) * ' ') + '+ '
    (s, q) = strs[0]
    q = select_quote(s, q, pref_q)
    if q == '"':
        s = escape_str(s)
    fd.write("%s%s%s\n" % (q, s, q))
    for (s, q) in strs[1:-1]:
        q = select_quote(s, q, pref_q)
        if q == '"':
            s = escape_str(s)
        fd.write("%s%s%s%s\n" % (prefix, q, s, q))
    (s, q) = strs[-1]
    q = select_quote(s, q, pref_q)
    if q == '"':
        s = escape_str(s)
    fd.write("%s%s%s%s" % (prefix, q, s, q))

    return need_new_line

def select_quote(s, q, pref_q):
    if pref_q == q:
        return q
    elif pref_q == "'":
        if s.find("'") == -1:
            # the string was double quoted, but it wasn't necessary,
            # use preferred single quote
            return "'"
        else:
            # the string was double quoted for a reason, keep it
            return '"'
    elif q == "'":
        if need_quote(s):
            # the string was single quoted for a reason, keep it
            return "'"
        else:
            # the string was single quoted but it wasn't necessary,
            # use preferred double quote
            return '"'

def escape_str(s):
    s = s.replace('\\', r'\\')
    s = s.replace('"', r'\"')
    s = s.replace('\t', r'\t')
    return s

def emit_path_arg(keywordstr, arg, fd, indent, max_line_len, line_len, eol):
    """Heuristically pretty print a path argument"""

    quote = '"'

    arg = escape_str(arg)

    if not(need_new_line(keywordstr, max_line_len, line_len, arg)):
        fd.write(" " + quote + arg + quote)
        return False

    ## FIXME: we should split the path on '/' and '[]' into multiple lines
    ## and then print each line

    num_chars = max_line_len - line_len
    if num_chars <= 0:
        # really small max_line_len; we give up
        fd.write(" " + quote + arg + quote)
        return False

    while num_chars > 2 and arg[num_chars - 1:num_chars].isalnum():
        num_chars -= 1
    fd.write(" " + quote + arg[:num_chars] + quote)
    arg = arg[num_chars:]
    keyword_cont = ((len(keywordstr) - 1) * ' ') + '+'
    while arg != '':
        line_len = len(
            "%s%s %s%s%s%s" % (indent, keyword_cont, quote, arg, quote, eol))
        num_chars = len(arg) - (line_len - max_line_len)
        while num_chars > 2 and arg[num_chars - 1:num_chars].isalnum():
            num_chars -= 1
        fd.write('\n' + indent + keyword_cont + " " +
                 quote + arg[:num_chars] + quote)
        arg = arg[num_chars:]

def emit_arg(keywordstr, stmt, fd, indent, indentstep, max_line_len, line_len):
    """Heuristically pretty print the argument string with double quotes"""
    arg = escape_str(stmt.arg)
    lines = arg.splitlines(True)
    if len(lines) <= 1:
        if len(arg) > 0 and arg[-1] == '\n':
            arg = arg[:-1] + r'\n'
        if (stmt.keyword in _force_newline_arg or
            need_new_line(keywordstr, max_line_len, line_len, arg)):
            fd.write('\n' + indent + indentstep + '"' + arg + '"')
            return True
        else:
            fd.write(' "' + arg + '"')
            return False
    else:
        if stmt.keyword in _force_newline_arg:
            fd.write('\n' + indent + indentstep)
            prefix = indent + indentstep
        else:
            fd.write(' ')
            prefix = indent + len(keywordstr) * ' ' + ' '
        fd.write('"' + lines[0])
        for line in lines[1:-1]:
            if line[0] == '\n':
                fd.write('\n')
            else:
                fd.write(prefix + ' ' + line)
        # write last line
        fd.write(prefix + ' ' + lines[-1])
        if lines[-1][-1] == '\n':
            # last line ends with a newline, indent the ending quote
            fd.write(prefix + '"')
        else:
            fd.write('"')
        return True

def emit_comment(comment, fd, indent):
    lines = comment.splitlines(True)
    for x in lines:
        if x[0] == '*':
            fd.write(indent + ' ' + x)
        else:
            fd.write(indent + x)
    fd.write('\n')

def need_quote(arg):
    for ch in _need_quote:
        if arg.find(ch) != -1:
            return True
    return False
