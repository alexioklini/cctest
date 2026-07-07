"""Workflow scripting engine — lexer, AST, parser, interpreter.

Self-contained DSL extracted from brain.py (governing principle #3: the
original copy is deleted from brain.py and re-exported via alias). The
`.flow` orchestration layer (WorkflowEngine / WorkflowExecution / history)
stays in brain.py because it is entangled with brain runtime state (DB,
AgentConfig, thread-locals); it imports these engine symbols via the alias.

Runtime brain access is lazy (per-call `import brain as _brain`) to keep the
engine/ -> brain dependency one-way (no import cycle). The only brain runtime
dependency is `TOOL_DISPATCH`, read fresh inside `_eval_call`.
"""

from __future__ import annotations

import datetime
import json

import re as _wf_re


class WorkflowError(Exception):
    """Raised on parse or runtime errors inside a workflow."""
    def __init__(self, message: str, line: int = 0):
        self.line = line
        super().__init__(message)


_WF_KEYWORDS = {
    "WORKFLOW", "DESCRIPTION", "TRIGGER", "AGENT", "MODEL",
    "SET", "CALL", "IF", "ELSE", "FOR", "EACH", "IN", "RETURN",
    "AND", "OR", "NOT", "TRUE", "FALSE", "NULL",
}

# Token types
_WF_TT = {
    "KEYWORD", "IDENT", "NUMBER", "STRING", "OP", "NEWLINE",
    "INDENT", "DEDENT", "COLON", "ASSIGN", "COMMA", "LPAREN", "RPAREN",
    "LBRACK", "RBRACK", "LBRACE", "RBRACE", "QMARK", "DOT", "EOF",
}


def _wf_tokenize(source: str) -> list[tuple]:
    """Indent-aware tokenizer. Returns list of (type, value, line) tuples."""
    tokens: list[tuple] = []
    indent_stack = [0]
    lines = source.split("\n")
    for lineno, raw_line in enumerate(lines, start=1):
        # Strip trailing comment (but not inside strings).
        # Simple approach: walk char by char, respect quotes.
        in_str = False
        quote = ""
        clean_chars = []
        i = 0
        while i < len(raw_line):
            c = raw_line[i]
            if in_str:
                clean_chars.append(c)
                if c == "\\" and i + 1 < len(raw_line):
                    clean_chars.append(raw_line[i + 1])
                    i += 2
                    continue
                if c == quote:
                    in_str = False
                i += 1
                continue
            if c in ('"', "'"):
                in_str = True
                quote = c
                clean_chars.append(c)
                i += 1
                continue
            if c == "#":
                break  # rest is comment
            clean_chars.append(c)
            i += 1
        line = "".join(clean_chars).rstrip()
        if not line.strip():
            continue  # blank or comment-only line — no INDENT/DEDENT change
        # Compute indent (spaces or tab=4)
        idx = 0
        col = 0
        while idx < len(line) and line[idx] in (" ", "\t"):
            col += 4 if line[idx] == "\t" else 1
            idx += 1
        body = line[idx:]
        # Emit INDENT / DEDENT
        if col > indent_stack[-1]:
            indent_stack.append(col)
            tokens.append(("INDENT", "", lineno))
        else:
            while col < indent_stack[-1]:
                indent_stack.pop()
                tokens.append(("DEDENT", "", lineno))
            if col != indent_stack[-1]:
                raise WorkflowError(f"Inconsistent indentation", lineno)
        # Tokenize body
        _wf_tok_line(body, lineno, tokens)
        tokens.append(("NEWLINE", "", lineno))
    while len(indent_stack) > 1:
        indent_stack.pop()
        tokens.append(("DEDENT", "", len(lines)))
    tokens.append(("EOF", "", len(lines)))
    return tokens


_WF_OP_MULTICHAR = ["==", "!=", "<=", ">=", "&&", "||"]


def _wf_tok_line(body: str, lineno: int, out: list[tuple]) -> None:
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c.isspace():
            i += 1
            continue
        # Strings
        if c in ('"', "'"):
            quote = c
            j = i + 1
            buf = []
            while j < n:
                ch = body[j]
                if ch == "\\" and j + 1 < n:
                    nxt = body[j + 1]
                    esc = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}.get(nxt, nxt)
                    buf.append(esc)
                    j += 2
                    continue
                if ch == quote:
                    j += 1
                    break
                buf.append(ch)
                j += 1
            else:
                raise WorkflowError("Unterminated string literal", lineno)
            out.append(("STRING", "".join(buf), lineno))
            i = j
            continue
        # Numbers
        if c.isdigit() or (c == "-" and i + 1 < n and body[i + 1].isdigit() and (not out or out[-1][0] in ("OP", "ASSIGN", "COMMA", "LPAREN", "LBRACK", "COLON", "INDENT", "NEWLINE"))):
            j = i + 1
            saw_dot = False
            while j < n and (body[j].isdigit() or (body[j] == "." and not saw_dot)):
                if body[j] == ".":
                    saw_dot = True
                j += 1
            num = body[i:j]
            try:
                val = float(num) if "." in num else int(num)
            except ValueError:
                raise WorkflowError(f"Invalid number: {num}", lineno)
            out.append(("NUMBER", val, lineno))
            i = j
            continue
        # Identifiers / keywords
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (body[j].isalnum() or body[j] == "_"):
                j += 1
            word = body[i:j]
            uw = word.upper()
            if uw in _WF_KEYWORDS:
                out.append(("KEYWORD", uw, lineno))
            else:
                out.append(("IDENT", word, lineno))
            i = j
            continue
        # Punctuation / operators
        if i + 1 < n and body[i:i + 2] in _WF_OP_MULTICHAR:
            op = body[i:i + 2]
            if op == "&&":
                op = "AND"
                out.append(("KEYWORD", op, lineno))
            elif op == "||":
                op = "OR"
                out.append(("KEYWORD", op, lineno))
            else:
                out.append(("OP", op, lineno))
            i += 2
            continue
        if c == "?":
            out.append(("QMARK", c, lineno))
            i += 1
            continue
        if c == "=":
            out.append(("ASSIGN", c, lineno))
            i += 1
            continue
        if c in "+-*/<>":
            out.append(("OP", c, lineno))
            i += 1
            continue
        if c == ":":
            out.append(("COLON", c, lineno))
            i += 1
            continue
        if c == ",":
            out.append(("COMMA", c, lineno))
            i += 1
            continue
        if c == "(":
            out.append(("LPAREN", c, lineno))
            i += 1
            continue
        if c == ")":
            out.append(("RPAREN", c, lineno))
            i += 1
            continue
        if c == "[":
            out.append(("LBRACK", c, lineno))
            i += 1
            continue
        if c == "]":
            out.append(("RBRACK", c, lineno))
            i += 1
            continue
        if c == "{":
            out.append(("LBRACE", c, lineno))
            i += 1
            continue
        if c == "}":
            out.append(("RBRACE", c, lineno))
            i += 1
            continue
        if c == ".":
            out.append(("DOT", c, lineno))
            i += 1
            continue
        raise WorkflowError(f"Unexpected character {c!r}", lineno)


# ----------------------------- AST -----------------------------


class _WFNode:
    pass


class _WFLiteral(_WFNode):
    __slots__ = ("value", "line")
    def __init__(self, value, line): self.value = value; self.line = line


class _WFVar(_WFNode):
    __slots__ = ("name", "line")
    def __init__(self, name, line): self.name = name; self.line = line


class _WFGetAttr(_WFNode):
    __slots__ = ("base", "attr", "line")
    def __init__(self, base, attr, line): self.base = base; self.attr = attr; self.line = line


class _WFGetItem(_WFNode):
    __slots__ = ("base", "index", "line")
    def __init__(self, base, index, line): self.base = base; self.index = index; self.line = line


class _WFBinOp(_WFNode):
    __slots__ = ("op", "left", "right", "line")
    def __init__(self, op, left, right, line): self.op = op; self.left = left; self.right = right; self.line = line


class _WFUnary(_WFNode):
    __slots__ = ("op", "operand", "line")
    def __init__(self, op, operand, line): self.op = op; self.operand = operand; self.line = line


class _WFFnCall(_WFNode):
    __slots__ = ("name", "args", "line")
    def __init__(self, name, args, line): self.name = name; self.args = args; self.line = line


class _WFList(_WFNode):
    __slots__ = ("items", "line")
    def __init__(self, items, line): self.items = items; self.line = line


class _WFDict(_WFNode):
    __slots__ = ("pairs", "line")
    def __init__(self, pairs, line): self.pairs = pairs; self.line = line


class _WFInterpStr(_WFNode):
    """A string literal with {{...}} placeholders. parts is list of (kind, value)."""
    __slots__ = ("parts", "line")
    def __init__(self, parts, line): self.parts = parts; self.line = line


class _WFAssign(_WFNode):
    __slots__ = ("name", "value", "line")
    def __init__(self, name, value, line): self.name = name; self.value = value; self.line = line


class _WFCall(_WFNode):
    """CALL <tool> name=value name=value  (statement form). soft=True for CALL?"""
    __slots__ = ("tool", "kwargs", "soft", "line")
    def __init__(self, tool, kwargs, soft, line): self.tool = tool; self.kwargs = kwargs; self.soft = soft; self.line = line


class _WFIf(_WFNode):
    __slots__ = ("cond", "then_body", "else_body", "line")
    def __init__(self, cond, then_body, else_body, line):
        self.cond = cond; self.then_body = then_body; self.else_body = else_body; self.line = line


class _WFFor(_WFNode):
    __slots__ = ("var", "iterable", "body", "line")
    def __init__(self, var, iterable, body, line):
        self.var = var; self.iterable = iterable; self.body = body; self.line = line


class _WFReturn(_WFNode):
    __slots__ = ("value", "line")
    def __init__(self, value, line): self.value = value; self.line = line


class _WFProgram(_WFNode):
    __slots__ = ("name", "description", "trigger", "agent_id", "model", "body")
    def __init__(self):
        self.name = ""
        self.description = ""
        self.trigger = "manual"
        self.agent_id = ""
        self.model = ""
        self.body: list[_WFNode] = []


# ----------------------------- Parser -----------------------------


class _WFParser:
    def __init__(self, tokens: list[tuple]):
        self.toks = tokens
        self.pos = 0

    def _peek(self, k=0) -> tuple:
        return self.toks[self.pos + k]

    def _eat(self, ttype: str, value=None) -> tuple:
        tok = self.toks[self.pos]
        if tok[0] != ttype:
            raise WorkflowError(f"Expected {ttype}, got {tok[0]} ({tok[1]!r})", tok[2])
        if value is not None and tok[1] != value:
            raise WorkflowError(f"Expected {value!r}, got {tok[1]!r}", tok[2])
        self.pos += 1
        return tok

    def _accept(self, ttype: str, value=None) -> tuple | None:
        tok = self.toks[self.pos]
        if tok[0] != ttype:
            return None
        if value is not None and tok[1] != value:
            return None
        self.pos += 1
        return tok

    def parse(self) -> _WFProgram:
        prog = _WFProgram()
        # Skip leading NEWLINEs
        while self._peek()[0] == "NEWLINE":
            self.pos += 1
        while self._peek()[0] != "EOF":
            tok = self._peek()
            if tok[0] == "KEYWORD" and tok[1] in ("WORKFLOW", "DESCRIPTION", "TRIGGER", "AGENT", "MODEL"):
                self._parse_header(prog)
                continue
            stmt = self._parse_statement()
            if stmt is not None:
                prog.body.append(stmt)
            # Eat trailing NEWLINEs at top level
            while self._peek()[0] == "NEWLINE":
                self.pos += 1
        return prog

    def _parse_header(self, prog: _WFProgram) -> None:
        kw = self._eat("KEYWORD")
        # Header value: a STRING literal OR a bare identifier OR a sequence of
        # IDENT/OP("-")/NUMBER tokens up to NEWLINE — supports hyphenated model
        # names like mistral-vibe-cli-fast without requiring quotes.
        nxt = self._peek()
        if nxt[0] == "STRING":
            value = nxt[1]
            self.pos += 1
        elif nxt[0] in ("IDENT", "NUMBER"):
            buf = [str(nxt[1])]
            self.pos += 1
            while self._peek()[0] != "NEWLINE":
                t = self._peek()
                if t[0] == "OP" and t[1] == "-":
                    buf.append("-")
                    self.pos += 1
                    continue
                if t[0] == "DOT":
                    buf.append(".")
                    self.pos += 1
                    continue
                if t[0] in ("IDENT", "NUMBER"):
                    buf.append(str(t[1]))
                    self.pos += 1
                    continue
                break
            value = "".join(buf)
        else:
            raise WorkflowError(f"Header {kw[1]} expects a string or identifier", kw[2])
        if kw[1] == "WORKFLOW":
            prog.name = value
        elif kw[1] == "DESCRIPTION":
            prog.description = value
        elif kw[1] == "TRIGGER":
            prog.trigger = value
        elif kw[1] == "AGENT":
            prog.agent_id = value
        elif kw[1] == "MODEL":
            prog.model = value
        # Trailing newline
        while self._peek()[0] == "NEWLINE":
            self.pos += 1

    def _parse_block(self) -> list[_WFNode]:
        # Expect INDENT, then statements until DEDENT
        self._eat("INDENT")
        stmts: list[_WFNode] = []
        while self._peek()[0] not in ("DEDENT", "EOF"):
            s = self._parse_statement()
            if s is not None:
                stmts.append(s)
            while self._peek()[0] == "NEWLINE":
                self.pos += 1
        if self._peek()[0] == "DEDENT":
            self.pos += 1
        return stmts

    def _parse_statement(self) -> _WFNode | None:
        tok = self._peek()
        if tok[0] == "NEWLINE":
            self.pos += 1
            return None
        if tok[0] == "KEYWORD":
            kw = tok[1]
            if kw == "SET":
                return self._parse_set()
            if kw == "CALL":
                return self._parse_call_stmt()
            if kw == "IF":
                return self._parse_if()
            if kw == "FOR":
                return self._parse_for()
            if kw == "RETURN":
                return self._parse_return()
        raise WorkflowError(f"Unexpected token at start of statement: {tok[0]} {tok[1]!r}", tok[2])

    def _parse_set(self) -> _WFAssign:
        line = self._peek()[2]
        self._eat("KEYWORD", "SET")
        name_tok = self._eat("IDENT")
        self._eat("ASSIGN")
        # Right-hand side may be a CALL expression OR a regular expression.
        if self._peek()[0] == "KEYWORD" and self._peek()[1] == "CALL":
            value = self._parse_call_expr()
        else:
            value = self._parse_expression()
        return _WFAssign(name_tok[1], value, line)

    def _parse_call_stmt(self) -> _WFCall:
        node = self._parse_call_expr()
        # Wrap in a statement node — _WFCall already serves both roles.
        return node  # type: ignore

    def _parse_call_expr(self) -> _WFCall:
        line = self._peek()[2]
        self._eat("KEYWORD", "CALL")
        soft = False
        if self._peek()[0] == "QMARK":
            self.pos += 1
            soft = True
        tool_tok = self._eat("IDENT")
        kwargs: list[tuple[str, _WFNode]] = []
        # kwargs are space-separated NAME=expr — terminate at NEWLINE/COLON/RBRACK/RPAREN/EOF
        while True:
            t = self._peek()
            if t[0] in ("NEWLINE", "COLON", "EOF", "RPAREN", "RBRACK", "RBRACE", "DEDENT"):
                break
            if t[0] == "COMMA":
                self.pos += 1
                continue
            # Argument names may collide with DSL keywords (the tokenizer
            # uppercases case-insensitively: `model=` → KEYWORD MODEL, same for
            # trigger/description/…). In kwarg-name position a KEYWORD followed
            # by `=` is unambiguous — accept it as a plain name (lowercased),
            # so tools with args like agent_step's `model` stay callable.
            if t[0] == "KEYWORD" and self._peek(1)[0] == "ASSIGN":
                self.pos += 1
                name_tok = (t[0], t[1].lower(), t[2])
            elif t[0] == "IDENT":
                name_tok = self._eat("IDENT")
            else:
                raise WorkflowError(f"Expected argument name, got {t[1]!r}", t[2])
            self._eat("ASSIGN")
            val = self._parse_expression()
            kwargs.append((name_tok[1], val))
        return _WFCall(tool_tok[1], kwargs, soft, line)

    def _parse_if(self) -> _WFIf:
        line = self._peek()[2]
        self._eat("KEYWORD", "IF")
        cond = self._parse_expression()
        self._eat("COLON")
        # Optional NEWLINE before block
        while self._peek()[0] == "NEWLINE":
            self.pos += 1
        then_body = self._parse_block()
        else_body: list[_WFNode] = []
        # Skip newlines and check for ELSE at the same indent
        while self._peek()[0] == "NEWLINE":
            self.pos += 1
        if self._peek()[0] == "KEYWORD" and self._peek()[1] == "ELSE":
            self._eat("KEYWORD", "ELSE")
            self._eat("COLON")
            while self._peek()[0] == "NEWLINE":
                self.pos += 1
            else_body = self._parse_block()
        return _WFIf(cond, then_body, else_body, line)

    def _parse_for(self) -> _WFFor:
        line = self._peek()[2]
        self._eat("KEYWORD", "FOR")
        self._eat("KEYWORD", "EACH")
        var_tok = self._eat("IDENT")
        self._eat("KEYWORD", "IN")
        iterable = self._parse_expression()
        self._eat("COLON")
        while self._peek()[0] == "NEWLINE":
            self.pos += 1
        body = self._parse_block()
        return _WFFor(var_tok[1], iterable, body, line)

    def _parse_return(self) -> _WFReturn:
        line = self._peek()[2]
        self._eat("KEYWORD", "RETURN")
        # Optional value
        if self._peek()[0] in ("NEWLINE", "EOF"):
            return _WFReturn(_WFLiteral(None, line), line)
        if self._peek()[0] == "KEYWORD" and self._peek()[1] == "CALL":
            value = self._parse_call_expr()
        else:
            value = self._parse_expression()
        return _WFReturn(value, line)

    # --- Expressions (Pratt-ish, low-to-high precedence) ---

    def _parse_expression(self) -> _WFNode:
        return self._parse_or()

    def _parse_or(self) -> _WFNode:
        left = self._parse_and()
        while self._peek()[0] == "KEYWORD" and self._peek()[1] == "OR":
            line = self._peek()[2]
            self.pos += 1
            right = self._parse_and()
            left = _WFBinOp("OR", left, right, line)
        return left

    def _parse_and(self) -> _WFNode:
        left = self._parse_not()
        while self._peek()[0] == "KEYWORD" and self._peek()[1] == "AND":
            line = self._peek()[2]
            self.pos += 1
            right = self._parse_not()
            left = _WFBinOp("AND", left, right, line)
        return left

    def _parse_not(self) -> _WFNode:
        if self._peek()[0] == "KEYWORD" and self._peek()[1] == "NOT":
            line = self._peek()[2]
            self.pos += 1
            return _WFUnary("NOT", self._parse_not(), line)
        return self._parse_compare()

    def _parse_compare(self) -> _WFNode:
        left = self._parse_addsub()
        while self._peek()[0] == "OP" and self._peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op_tok = self._peek()
            self.pos += 1
            right = self._parse_addsub()
            left = _WFBinOp(op_tok[1], left, right, op_tok[2])
        return left

    def _parse_addsub(self) -> _WFNode:
        left = self._parse_muldiv()
        while self._peek()[0] == "OP" and self._peek()[1] in ("+", "-"):
            op_tok = self._peek()
            self.pos += 1
            right = self._parse_muldiv()
            left = _WFBinOp(op_tok[1], left, right, op_tok[2])
        return left

    def _parse_muldiv(self) -> _WFNode:
        left = self._parse_unary()
        while self._peek()[0] == "OP" and self._peek()[1] in ("*", "/"):
            op_tok = self._peek()
            self.pos += 1
            right = self._parse_unary()
            left = _WFBinOp(op_tok[1], left, right, op_tok[2])
        return left

    def _parse_unary(self) -> _WFNode:
        if self._peek()[0] == "OP" and self._peek()[1] == "-":
            line = self._peek()[2]
            self.pos += 1
            return _WFUnary("-", self._parse_unary(), line)
        return self._parse_postfix()

    def _parse_postfix(self) -> _WFNode:
        node = self._parse_atom()
        while True:
            t = self._peek()
            if t[0] == "DOT":
                self.pos += 1
                attr = self._eat("IDENT")
                node = _WFGetAttr(node, attr[1], attr[2])
                continue
            if t[0] == "LBRACK":
                self.pos += 1
                idx_expr = self._parse_expression()
                self._eat("RBRACK")
                node = _WFGetItem(node, idx_expr, t[2])
                continue
            break
        return node

    def _parse_atom(self) -> _WFNode:
        t = self._peek()
        if t[0] == "NUMBER":
            self.pos += 1
            return _WFLiteral(t[1], t[2])
        if t[0] == "STRING":
            self.pos += 1
            return self._make_interp(t[1], t[2])
        if t[0] == "KEYWORD" and t[1] in ("TRUE", "FALSE", "NULL"):
            self.pos += 1
            v = True if t[1] == "TRUE" else False if t[1] == "FALSE" else None
            return _WFLiteral(v, t[2])
        if t[0] == "IDENT":
            self.pos += 1
            # Function call syntax: foo(arg, arg)
            if self._peek()[0] == "LPAREN":
                self.pos += 1
                args: list[_WFNode] = []
                if self._peek()[0] != "RPAREN":
                    args.append(self._parse_expression())
                    while self._peek()[0] == "COMMA":
                        self.pos += 1
                        args.append(self._parse_expression())
                self._eat("RPAREN")
                return _WFFnCall(t[1], args, t[2])
            return _WFVar(t[1], t[2])
        if t[0] == "LPAREN":
            self.pos += 1
            inner = self._parse_expression()
            self._eat("RPAREN")
            return inner
        if t[0] == "LBRACK":
            self.pos += 1
            items: list[_WFNode] = []
            if self._peek()[0] != "RBRACK":
                items.append(self._parse_expression())
                while self._peek()[0] == "COMMA":
                    self.pos += 1
                    items.append(self._parse_expression())
            self._eat("RBRACK")
            return _WFList(items, t[2])
        if t[0] == "LBRACE":
            self.pos += 1
            pairs: list[tuple[_WFNode, _WFNode]] = []
            if self._peek()[0] != "RBRACE":
                pairs.append(self._parse_dict_pair())
                while self._peek()[0] == "COMMA":
                    self.pos += 1
                    pairs.append(self._parse_dict_pair())
            self._eat("RBRACE")
            return _WFDict(pairs, t[2])
        raise WorkflowError(f"Unexpected token {t[0]} {t[1]!r}", t[2])

    def _parse_dict_pair(self) -> tuple[_WFNode, _WFNode]:
        # key may be IDENT or STRING
        key_tok = self._peek()
        if key_tok[0] == "IDENT":
            self.pos += 1
            key_node: _WFNode = _WFLiteral(key_tok[1], key_tok[2])
        elif key_tok[0] == "STRING":
            self.pos += 1
            key_node = _WFLiteral(key_tok[1], key_tok[2])
        else:
            raise WorkflowError(f"Expected dict key, got {key_tok[1]!r}", key_tok[2])
        self._eat("COLON")
        val_node = self._parse_expression()
        return (key_node, val_node)

    def _make_interp(self, raw: str, line: int) -> _WFNode:
        """Convert a string with {{...}} into _WFInterpStr or plain _WFLiteral."""
        if "{{" not in raw:
            return _WFLiteral(raw, line)
        parts: list[tuple[str, str]] = []
        i = 0
        n = len(raw)
        buf: list[str] = []
        while i < n:
            if raw[i:i + 2] == "{{":
                if buf:
                    parts.append(("text", "".join(buf)))
                    buf = []
                j = raw.find("}}", i + 2)
                if j == -1:
                    raise WorkflowError("Unterminated {{ in string", line)
                expr_src = raw[i + 2:j].strip()
                parts.append(("expr", expr_src))
                i = j + 2
            else:
                buf.append(raw[i])
                i += 1
        if buf:
            parts.append(("text", "".join(buf)))
        return _WFInterpStr(parts, line)


# ----------------------------- Parser entry point -----------------------------


def _wf_parse(source: str) -> _WFProgram:
    toks = _wf_tokenize(source)
    parser = _WFParser(toks)
    return parser.parse()


# Builtin function names the interpreter accepts (see _eval_builtin) — used by
# the workflow generator's validation pass to reject unknown functions before
# a run ever starts.
_WF_BUILTINS = frozenset({
    "len", "str", "int", "float", "bool", "now", "lower", "upper", "trim",
    "contains", "split", "join", "replace", "plan_steps",
})


# ----------------------------- Plan splitting -----------------------------

# Step headings a plan markdown may use: "### Schritt 3 — Titel", "## Step 2:",
# "### 4. Titel". Deterministic code, no LLM — the `plan_steps()` DSL builtin
# splits a plan.md into per-step sections for FOR EACH … agent_step loops.
_WF_PLAN_STEP_RE = _wf_re.compile(
    r"^#{2,4}\s*(?:(?:Schritt|Step|Phase)\s*\d+|\d+[.)])\b.*$",
    _wf_re.IGNORECASE | _wf_re.MULTILINE)
_WF_PLAN_ANY_HEADING_RE = _wf_re.compile(r"^#{2,4}\s+\S.*$", _wf_re.MULTILINE)


def _plan_steps(md: str) -> list[dict]:
    """Split a plan markdown into [{index, title, body}] on step headings.
    Prefers explicit Schritt/Step/numbered headings; falls back to any ##/###
    heading; a plan with no headings at all becomes one single step."""
    text = (md or "").strip()
    if not text:
        return []
    matches = list(_WF_PLAN_STEP_RE.finditer(text))
    if len(matches) < 2:
        matches = list(_WF_PLAN_ANY_HEADING_RE.finditer(text))
    if not matches:
        return [{"index": 1, "title": "", "body": text}]
    steps: list[dict] = []
    for i, m in enumerate(matches):
        title = _wf_re.sub(r"^#+\s*", "", m.group(0)).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        steps.append({"index": i + 1, "title": title,
                      "body": (title + "\n" + body).strip() if body else title})
    return steps


# ----------------------------- Interpreter -----------------------------


class _WFReturnValue(Exception):
    def __init__(self, value): self.value = value


class _WorkflowInterpreter:
    """Executes a parsed _WFProgram. Reports progress via a status callback."""

    def __init__(self, program: _WFProgram, execution: "WorkflowExecution"):
        self.program = program
        self.execution = execution
        self.env: dict[str, object] = dict(execution.variables or {})
        # last_error available to scripts
        self.env.setdefault("last_error", None)

    def run(self) -> object:
        try:
            for node in self.program.body:
                if self.execution._cancel.is_set():
                    return None
                self._exec(node)
        except _WFReturnValue as rv:
            self.execution._return_value = rv.value
            return rv.value
        return None

    # --- Statement dispatch ---

    def _exec(self, node: _WFNode) -> None:
        if isinstance(node, _WFAssign):
            value = self._eval(node.value)
            self.env[node.name] = value
            self._emit_step(node.line, "set", f"{node.name} = {self._summary(value)}")
            return
        if isinstance(node, _WFCall):
            value = self._eval_call(node)
            self.env["_"] = value
            return
        if isinstance(node, _WFIf):
            cond = self._eval(node.cond)
            self._emit_step(node.line, "if", f"condition = {bool(cond)}")
            body = node.then_body if cond else node.else_body
            for child in body:
                if self.execution._cancel.is_set():
                    return
                self._exec(child)
            return
        if isinstance(node, _WFFor):
            iterable = self._eval(node.iterable)
            if not hasattr(iterable, "__iter__"):
                raise WorkflowError(f"FOR EACH expects an iterable, got {type(iterable).__name__}", node.line)
            count = 0
            for item in iterable:
                if self.execution._cancel.is_set():
                    return
                self.env[node.var] = item
                count += 1
                self._emit_step(node.line, "for", f"{node.var} = {self._summary(item)} (iter {count})")
                for child in node.body:
                    if self.execution._cancel.is_set():
                        return
                    self._exec(child)
            return
        if isinstance(node, _WFReturn):
            value = self._eval(node.value)
            raise _WFReturnValue(value)
        raise WorkflowError(f"Unknown statement type: {type(node).__name__}", getattr(node, "line", 0))

    # --- Expression eval ---

    def _eval(self, node: _WFNode) -> object:
        if isinstance(node, _WFLiteral):
            return node.value
        if isinstance(node, _WFVar):
            if node.name not in self.env:
                raise WorkflowError(f"Undefined variable: {node.name}", node.line)
            return self.env[node.name]
        if isinstance(node, _WFGetAttr):
            base = self._eval(node.base)
            return self._get_field(base, node.attr, node.line)
        if isinstance(node, _WFGetItem):
            base = self._eval(node.base)
            idx = self._eval(node.index)
            try:
                return base[idx]
            except (KeyError, IndexError, TypeError) as e:
                raise WorkflowError(f"Index error: {e}", node.line)
        if isinstance(node, _WFBinOp):
            return self._eval_binop(node)
        if isinstance(node, _WFUnary):
            v = self._eval(node.operand)
            if node.op == "-":
                return -v
            if node.op == "NOT":
                return not v
            raise WorkflowError(f"Unknown unary op {node.op}", node.line)
        if isinstance(node, _WFFnCall):
            return self._eval_builtin(node)
        if isinstance(node, _WFList):
            return [self._eval(it) for it in node.items]
        if isinstance(node, _WFDict):
            out: dict = {}
            for k, v in node.pairs:
                kv = self._eval(k)
                vv = self._eval(v)
                out[kv] = vv
            return out
        if isinstance(node, _WFInterpStr):
            buf: list[str] = []
            for kind, val in node.parts:
                if kind == "text":
                    buf.append(val)
                else:
                    expr_node = _wf_parse(f"SET _x = {val}").body[0]  # type: ignore
                    res = self._eval(expr_node.value)  # type: ignore
                    buf.append("" if res is None else str(res))
            return "".join(buf)
        if isinstance(node, _WFCall):
            return self._eval_call(node)
        raise WorkflowError(f"Unknown expression node: {type(node).__name__}", getattr(node, "line", 0))

    def _eval_binop(self, node: _WFBinOp) -> object:
        op = node.op
        if op == "AND":
            return bool(self._eval(node.left)) and bool(self._eval(node.right))
        if op == "OR":
            return bool(self._eval(node.left)) or bool(self._eval(node.right))
        a = self._eval(node.left)
        b = self._eval(node.right)
        if op == "+":
            if isinstance(a, str) or isinstance(b, str):
                return str(a) + str(b)
            return a + b
        if op == "-": return a - b
        if op == "*": return a * b
        if op == "/": return a / b
        if op == "==": return a == b
        if op == "!=": return a != b
        if op == "<":  return a < b
        if op == ">":  return a > b
        if op == "<=": return a <= b
        if op == ">=": return a >= b
        raise WorkflowError(f"Unknown binop {op}", node.line)

    def _eval_builtin(self, node: _WFFnCall) -> object:
        # Names must stay in sync with _WF_BUILTINS (used by the generator's
        # deterministic validation pass).
        name = node.name
        args = [self._eval(a) for a in node.args]
        try:
            if name == "len":
                return len(args[0])
            if name == "str":
                return "" if args[0] is None else str(args[0])
            if name == "int":
                return int(args[0])
            if name == "float":
                return float(args[0])
            if name == "bool":
                return bool(args[0])
            if name == "now":
                fmt = args[0] if args else "%Y-%m-%dT%H:%M:%S"
                return datetime.datetime.now().strftime(fmt)
            if name == "lower":
                return str(args[0]).lower()
            if name == "upper":
                return str(args[0]).upper()
            if name == "trim":
                return str(args[0]).strip()
            if name == "contains":
                return args[1] in args[0]
            if name == "split":
                return str(args[0]).split(args[1]) if len(args) > 1 else str(args[0]).split()
            if name == "join":
                sep = args[0]
                items = args[1]
                return sep.join(str(x) for x in items)
            if name == "replace":
                return str(args[0]).replace(args[1], args[2])
            if name == "plan_steps":
                return _plan_steps(str(args[0]) if args else "")
        except Exception as e:
            raise WorkflowError(f"{name}(): {e}", node.line)
        raise WorkflowError(f"Unknown function: {name}", node.line)

    def _eval_call(self, node: _WFCall) -> object:
        tool = node.tool
        # Resolve tool from TOOL_DISPATCH. Lazy brain import keeps engine/ -> brain
        # one-way (no import cycle); read fresh per call so late tool registration
        # is visible.
        import brain as _brain
        fn = _brain.TOOL_DISPATCH.get(tool)
        if not fn:
            raise WorkflowError(f"Unknown tool: {tool}", node.line)
        kwargs = {}
        for k, expr in node.kwargs:
            kwargs[k] = self._eval(expr)
        # ask_user_for_file: include prompt + accept in the step detail so the
        # frontend's polling loop can render a meaningful upload UI without a
        # second event channel. Other tools keep redacted args to avoid leaking
        # secrets/long values into the steps log.
        if tool == "ask_user_for_file":
            ufp = str(kwargs.get("prompt", "") or "").replace("|", " ")
            ufa = str(kwargs.get("accept", "") or "").replace("|", " ")
            self._emit_step(node.line, "call", f"{tool}(prompt={ufp!r}, accept={ufa!r})")
        else:
            self._emit_step(node.line, "call", f"{tool}({', '.join(f'{k}=…' for k, _ in node.kwargs)})")
        try:
            result_str = fn(kwargs)
        except Exception as e:
            if node.soft:
                self.env["last_error"] = str(e)
                self._emit_step(node.line, "call_soft_error", f"{tool}: {e}")
                return None
            raise WorkflowError(f"Tool {tool} raised: {e}", node.line)
        # Parse JSON envelope
        try:
            parsed = json.loads(result_str) if isinstance(result_str, str) else result_str
        except Exception:
            # Tool returned non-JSON string; pass through
            self._emit_step(node.line, "call_done", f"{tool} → (raw text)")
            return result_str
        if isinstance(parsed, dict) and "error" in parsed:
            err = parsed.get("error", "unknown error")
            if node.soft:
                self.env["last_error"] = err
                self._emit_step(node.line, "call_soft_error", f"{tool}: {err}")
                return None
            raise WorkflowError(f"Tool {tool} failed: {err}", node.line)
        self.env["last_error"] = None
        self._emit_step(node.line, "call_done", f"{tool} → {self._summary(parsed)}")
        # Chat-view transcript + reliable output paths, captured UNTRUNCATED
        # (steps above truncate to 120 chars — useless for a real chat view /
        # artifact seeding). Only the meaningful nodes contribute a turn.
        try:
            self._capture_transcript(node.line, tool, kwargs, parsed)
        except Exception:
            pass  # capture is best-effort; never break a run over it
        return parsed

    def _capture_transcript(self, line: int, tool: str, kwargs: dict, parsed) -> object:
        """Record chat turns + output paths from a tool call's FULL result.

        The transcript reconstructs the run AS IF a user had driven it in chat:
        each agent_step becomes a USER turn (the instruction — "the request")
        followed by an ASSISTANT turn (the full LLM answer). ask_user_for_file
        is a user turn (the upload). So the run reads as a real Q&A dialogue.

        - ask_user_for_file → a user turn (prompt + uploaded file path)
        - agent_step        → a user turn (instruction) + an assistant turn
                              (full text + model + written files)
        - write_file/edit_file → record the full output path (untruncated)
        """
        ex = self.execution
        if tool == "ask_user_for_file":
            prompt = str(kwargs.get("prompt", "") or "").strip()
            files = []
            if isinstance(parsed, dict) and parsed.get("path"):
                files.append(parsed["path"])
            ex.record_message("user", prompt or "Datei hochgeladen", files=files, line=line)
        elif tool == "agent_step":
            # User turn = the instruction the workflow gave the agent (this is
            # "the request" a chat user would have typed). Uploaded input files
            # ride along so they show as attachments on the request, like chat.
            instruction = str(kwargs.get("instruction", "") or "").strip()
            in_files = []
            for f in (kwargs.get("files") or []):
                fp = f.get("path") if isinstance(f, dict) else f
                if fp:
                    in_files.append(str(fp))
            if instruction:
                ex.record_message("user", instruction, files=in_files, line=line)
            # Assistant turn = the agent's full answer.
            text = ""
            files = []
            model = ""
            if isinstance(parsed, dict):
                text = str(parsed.get("text") or "")
                model = str(parsed.get("model") or "")
                for p in (parsed.get("files") or []):
                    if p:
                        files.append(p)
                        ex.record_output_path(p)
            ex.record_message("assistant", text, model=model, files=files, line=line)
        elif tool in ("write_file", "edit_file"):
            p = ""
            if isinstance(parsed, dict):
                p = str(parsed.get("path") or "")
            if not p:
                p = str(kwargs.get("path") or "")
            if p:
                ex.record_output_path(p)
                # A workflow-level write_file (e.g. `write_file content=r.text`)
                # produces the deliverable AFTER the agent_step, as its own DSL
                # node — so attach it to the most recent assistant turn so it
                # renders as an artifact-card ON that answer (like a chat where
                # the assistant produced the file). Falls back to a fresh
                # assistant turn if there's no prior one.
                ex.attach_output_to_last_answer(p)
        return parsed

    def _get_field(self, obj, attr: str, line: int) -> object:
        if isinstance(obj, dict):
            if attr in obj:
                return obj[attr]
            raise WorkflowError(f"Field '{attr}' not in dict (keys: {list(obj.keys())})", line)
        if hasattr(obj, attr):
            return getattr(obj, attr)
        raise WorkflowError(f"Cannot access field '{attr}' on {type(obj).__name__}", line)

    def _summary(self, value) -> str:
        s = str(value)
        if len(s) > 120:
            s = s[:117] + "..."
        return s

    def _emit_step(self, line: int, kind: str, detail: str) -> None:
        self.execution._record_step(line, kind, detail)

