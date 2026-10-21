"""Bounded interpreter for a small Python subset. Never eval/exec/import candidate code.

Only data values and this module's operators are reachable. Syntax is checked on
the entire tree, including dead branches. Each input starts with fresh locals.
"""
import ast
import operator

MAX_SOURCE_BYTES = 8000
MAX_NODES = 600
MAX_STEPS = 6000
MAX_DEPTH = 32
MAX_VALUE = 1_000_000

LANGUAGE = """Return an entire Python module, not a diff or Markdown. Define decision with
exactly the original positional parameter names. At most 8 top-level functions;
helpers may call other helpers, but no recursion. No top-level statements except
function definitions and docstrings. Function bodies: local assignment, if/else,
return, pass, docstrings. Expressions: names, bool/int/string/None constants,
lists/tuples/sets/dicts, indexing (no slices), and/or/not, if-expressions,
comparisons (is/is not only for bool/None), membership, integer + - * // % and bitwise & | ^, unary +/-/~,
calls to your helper functions and bool/int/len/abs/min/max/all/any/sum.
No imports, attributes/methods, decorators, annotations, loops, comprehensions,
lambdas, defaults, global/nonlocal, mutation, I/O or exceptions. No names starting
with underscore. Strings <=256 characters, integers <=1,000,000 in magnitude,
source <=8000 UTF-8 bytes. Return exactly bool for every specified input.
Comments and docstrings are allowed, but there is no filesystem or network.
"""


class InvalidCandidate(ValueError):
    pass


class Returned(Exception):
    def __init__(self, value):
        self.value = value


BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
          ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}
COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
           ast.Is: operator.is_, ast.IsNot: operator.is_not,
           ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
BUILTINS = {"bool": bool, "int": int, "len": len, "abs": abs, "min": min,
            "max": max, "all": all, "any": any, "sum": sum}
ALLOWED = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
           ast.Assign, ast.If, ast.Expr, ast.Pass, ast.Name, ast.Load, ast.Store,
           ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Subscript,
           ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.UAdd, ast.USub,
           ast.Invert, ast.BinOp, ast.Compare, ast.IfExp, ast.Call,
           *BINARY.keys(), *COMPARE.keys())


def checked(value, depth=0, budget=None):
    if budget is None:
        budget = [2048]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        raise InvalidCandidate("Nested value budget exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is int and abs(value) <= MAX_VALUE:
        return value
    if type(value) is str and len(value) <= 256:
        return value
    if type(value) in (tuple, list, set, dict) and len(value) <= 64:
        for item in value:
            checked(item, depth + 1, budget)
            if type(value) is dict:
                checked(value[item], depth + 1, budget)
        return value
    raise InvalidCandidate("Value outside bounded data subset")


class Program:
    def __init__(self, source, parameters):
        if type(source) is not str or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise InvalidCandidate("Source must be <=8000 UTF-8 bytes")
        try:
            self.tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise InvalidCandidate(f"Invalid Python syntax: {type(exc).__name__}") from exc
        nodes = list(ast.walk(self.tree))
        if len(nodes) > MAX_NODES:
            raise InvalidCandidate("Too many syntax nodes")
        self.functions = {}
        for node in nodes:
            if not isinstance(node, ALLOWED):
                raise InvalidCandidate(f"Unsupported syntax: {type(node).__name__}")
            if isinstance(node, ast.Constant):
                checked(node.value)
            if isinstance(node, (ast.Name, ast.arg)):
                name = node.id if isinstance(node, ast.Name) else node.arg
                if name.startswith("_"):
                    raise InvalidCandidate("Names starting with underscore are not allowed")
            if isinstance(node, ast.Expr) and not (
                isinstance(node.value, ast.Constant) and type(node.value.value) is str
            ):
                raise InvalidCandidate("Only docstrings can be expression statements")
            if isinstance(node, ast.Assign) and (
                len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name)
            ):
                raise InvalidCandidate("Only single local-name assignments are allowed")
            if isinstance(node, ast.Call) and (
                not isinstance(node.func, ast.Name) or node.keywords
            ):
                raise InvalidCandidate("Calls must use a named helper and positional arguments")
        for node in self.tree.body:
            if isinstance(node, ast.Expr):
                continue
            if not isinstance(node, ast.FunctionDef):
                raise InvalidCandidate("Module may contain only functions and docstrings")
            args = node.args
            if (node.name.startswith("_") or node.name in BUILTINS or
                node.name in self.functions or node.decorator_list or node.returns or
                args.posonlyargs or args.vararg or args.kwarg or args.kwonlyargs or
                args.defaults or any(a.annotation for a in args.args) or
                len({a.arg for a in args.args}) != len(args.args)):
                raise InvalidCandidate("Unsupported function signature or duplicate name")
            self.functions[node.name] = node
        if not 1 <= len(self.functions) <= 8 or "decision" not in self.functions:
            raise InvalidCandidate("Define decision and at most 7 helpers")
        if sum(isinstance(n, ast.FunctionDef) for n in nodes) != len(self.functions):
            raise InvalidCandidate("Nested functions are not supported")
        if tuple(a.arg for a in self.functions["decision"].args.args) != tuple(parameters):
            raise InvalidCandidate("decision signature differs from original")
        self.local_names = {}
        graph = {}
        for name, fn in self.functions.items():
            names = {a.arg for a in fn.args.args}
            names.update(n.targets[0].id for n in ast.walk(fn) if isinstance(n, ast.Assign))
            if names & (set(self.functions) | set(BUILTINS)):
                raise InvalidCandidate("Local names must not shadow functions or builtins")
            self.local_names[name] = names
            graph[name] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    called = node.func.id
                    if called not in self.functions and called not in BUILTINS:
                        raise InvalidCandidate(f"Unknown function: {called}")
                    if called in self.functions:
                        graph[name].add(called)
        def visit(name, stack):
            if name in stack:
                raise InvalidCandidate("Recursive calls are not allowed")
            for child in graph[name]:
                visit(child, stack | {name})
        for name in graph:
            visit(name, set())
        self.steps = 0

    def tick(self, depth):
        self.steps += 1
        if self.steps > MAX_STEPS or depth > MAX_DEPTH:
            raise InvalidCandidate("Execution budget exceeded")

    def run(self, args):
        self.steps = 0
        try:
            value = self.call("decision", list(args), 0)
            if type(value) is not bool:
                raise InvalidCandidate("decision must return exactly bool")
            return value
        except InvalidCandidate:
            raise
        except (ArithmeticError, TypeError, ValueError, KeyError, IndexError, RecursionError) as exc:
            raise InvalidCandidate(f"Candidate runtime error: {type(exc).__name__}") from exc

    def call(self, name, args, depth):
        self.tick(depth)
        if name in BUILTINS:
            return checked(BUILTINS[name](*args))
        fn = self.functions[name]
        if len(args) != len(fn.args.args):
            raise InvalidCandidate("Wrong helper argument count")
        env = dict(zip((a.arg for a in fn.args.args), args))
        try:
            self.block(fn.body, env, depth + 1)
        except Returned as ret:
            return checked(ret.value)
        return None

    def block(self, nodes, env, depth):
        for node in nodes:
            self.tick(depth)
            if isinstance(node, ast.Return):
                raise Returned(self.expr(node.value, env, depth + 1) if node.value else None)
            if isinstance(node, ast.Assign):
                env[node.targets[0].id] = self.expr(node.value, env, depth + 1)
            elif isinstance(node, ast.If):
                branch = node.body if self.expr(node.test, env, depth + 1) else node.orelse
                self.block(branch, env, depth + 1)
            elif not isinstance(node, (ast.Expr, ast.Pass)):
                raise InvalidCandidate("Unsupported statement")

    def expr(self, node, env, depth):
        self.tick(depth)
        def ev(n):
            return self.expr(n, env, depth + 1)
        if isinstance(node, ast.Constant):
            value = node.value
        elif isinstance(node, ast.Name):
            if node.id not in env:
                raise InvalidCandidate(f"Unbound local: {node.id}")
            value = env[node.id]
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            factory = {ast.List: list, ast.Tuple: tuple, ast.Set: set}[type(node)]
            value = factory(ev(x) for x in node.elts)
        elif isinstance(node, ast.Dict):
            if any(k is None for k in node.keys):
                raise InvalidCandidate("Dictionary unpacking is not supported")
            value = {ev(k): ev(v) for k, v in zip(node.keys, node.values)}
        elif isinstance(node, ast.Subscript):
            value = ev(node.value)[ev(node.slice)]
        elif isinstance(node, ast.BoolOp):
            value = ev(node.values[0])
            for child in node.values[1:]:
                if isinstance(node.op, ast.And) and not value:
                    break
                if isinstance(node.op, ast.Or) and value:
                    break
                value = ev(child)
        elif isinstance(node, ast.UnaryOp):
            arg = ev(node.operand)
            if isinstance(node.op, ast.Not):
                value = not arg
            else:
                if type(arg) not in (bool, int):
                    raise InvalidCandidate("Unary arithmetic requires integers")
                value = {ast.UAdd: operator.pos, ast.USub: operator.neg,
                         ast.Invert: operator.invert}[type(node.op)](arg)
        elif isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if type(left) not in (bool, int) or type(right) not in (bool, int):
                raise InvalidCandidate("Binary arithmetic requires integers")
            value = BINARY[type(node.op)](left, right)
        elif isinstance(node, ast.Compare):
            left = ev(node.left)
            value = True
            for op, right_node in zip(node.ops, node.comparators):
                right = ev(right_node)
                if isinstance(op, (ast.Is, ast.IsNot)) and any(
                    operand is not None and type(operand) is not bool for operand in (left, right)
                ):
                    raise InvalidCandidate("Identity comparison is only defined for bool/None in this subset")
                if not COMPARE[type(op)](left, right):
                    value = False
                    break
                left = right
        elif isinstance(node, ast.IfExp):
            value = ev(node.body if ev(node.test) else node.orelse)
        elif isinstance(node, ast.Call):
            value = self.call(node.func.id, [ev(arg) for arg in node.args], depth + 1)
        else:
            raise InvalidCandidate(f"Unsupported expression: {type(node).__name__}")
        return checked(value)
