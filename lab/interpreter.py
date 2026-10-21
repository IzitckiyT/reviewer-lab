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
    def __init__(self, reason, *, message=None, line=None, column=None, kind="invalid"):
        super().__init__(reason)
        self.diagnostic = {"message": message or reason, "line": line,
                           "column": column, "kind": kind}


class Returned(Exception):
    def __init__(self, value, node=None):
        self.value = value
        self.node = node


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

UNSUPPORTED_MESSAGES = {
    "Import": "Импорты не поддерживаются. Уберите import и используйте локальные функции.",
    "ImportFrom": "Импорты не поддерживаются. Уберите import и используйте локальные функции.",
    "For": "Циклы не поддерживаются. Используйте условия или операции над готовыми данными.",
    "AsyncFor": "Циклы не поддерживаются. Используйте условия или операции над готовыми данными.",
    "While": "Циклы не поддерживаются. Используйте условия или операции над готовыми данными.",
    "ListComp": "Генераторы коллекций не поддерживаются. Запишите элементы явно.",
    "SetComp": "Генераторы коллекций не поддерживаются. Запишите элементы явно.",
    "DictComp": "Генераторы коллекций не поддерживаются. Запишите элементы явно.",
    "GeneratorExp": "Генераторные выражения не поддерживаются. Используйте готовую коллекцию.",
    "Lambda": "Лямбда-функции не поддерживаются. Вынесите логику в отдельную функцию.",
    "Attribute": "Атрибуты и методы не поддерживаются. Используйте локальные функции.",
    "AnnAssign": "Аннотации типов не поддерживаются. Уберите тип из присваивания.",
    "AugAssign": "Составное присваивание не поддерживается. Например, замените x += 1 на x = x + 1.",
    "Slice": "Срезы не поддерживаются. Обращайтесь к элементу по одному индексу.",
    "Try": "Обработка исключений не поддерживается. Проверьте входные данные условием.",
    "Raise": "Вызов исключений не поддерживается. Функция decision должна вернуть True или False.",
    "ClassDef": "Классы не поддерживаются. Используйте функции и простые данные.",
}


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
            message = ("Код превышает лимит 8000 байт UTF-8. Сократите код или комментарии; "
                       "русские буквы занимают больше одного байта."
                       if type(source) is str else "Вставьте код функции decision как текст.")
            raise InvalidCandidate("Source must be <=8000 UTF-8 bytes",
                                   message=message, kind="size" if type(source) is str else "invalid")
        self.source = source
        try:
            self.tree = ast.parse(source)
        except SyntaxError as exc:
            guidance = ("Не хватает двоеточия (:)." if exc.msg == "expected ':'" else
                        f"Проверьте отступы: {exc.msg}." if isinstance(exc, IndentationError) else
                        f"Исправьте синтаксис Python: {exc.msg}.")
            raise InvalidCandidate(f"Invalid Python syntax: {type(exc).__name__}",
                                   message=guidance, line=exc.lineno,
                                   column=exc.offset if exc.offset and exc.offset > 0 else None,
                                   kind="syntax") from exc
        except (ValueError, RecursionError) as exc:
            raise InvalidCandidate(f"Invalid Python syntax: {type(exc).__name__}") from exc
        nodes = list(ast.walk(self.tree))
        if len(nodes) > MAX_NODES:
            raise InvalidCandidate("Too many syntax nodes")
        self.functions = {}
        for node in nodes:
            if not isinstance(node, ALLOWED):
                name = type(node).__name__
                raise self.invalid(f"Unsupported syntax: {name}", node,
                                   message=UNSUPPORTED_MESSAGES.get(name), kind="unsupported")
            if isinstance(node, ast.Constant):
                checked(node.value)
            if isinstance(node, (ast.Name, ast.arg)):
                name = node.id if isinstance(node, ast.Name) else node.arg
                if name.startswith("_"):
                    raise InvalidCandidate("Names starting with underscore are not allowed")
            if isinstance(node, ast.Expr) and not (
                isinstance(node.value, ast.Constant) and type(node.value.value) is str
            ):
                raise self.invalid("Only docstrings can be expression statements", node,
                                   message="Отдельные выражения не поддерживаются. "
                                           "Используйте присваивание или return.", kind="unsupported")
            if isinstance(node, ast.Assign) and (
                len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name)
            ):
                raise InvalidCandidate("Only single local-name assignments are allowed")
            if isinstance(node, ast.Call) and (
                not isinstance(node.func, ast.Name) or node.keywords
            ):
                raise self.invalid("Calls must use a named helper and positional arguments", node,
                                   message="Вызывайте функцию по имени с позиционными аргументами. "
                                           "Методы и именованные аргументы не поддерживаются.",
                                   kind="unsupported")
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

    def invalid(self, reason, node=None, *, message=None, kind="invalid"):
        line = getattr(node, "lineno", None)
        offset = getattr(node, "col_offset", None)
        column = None
        if line is not None and offset is not None:
            # AST offsets count UTF-8 bytes; the editor counts visible characters.
            prefix = self.source.splitlines()[line - 1].encode("utf-8")[:offset]
            column = len(prefix.decode("utf-8")) + 1
        return InvalidCandidate(reason, message=message, line=line, column=column, kind=kind)

    def tick(self, depth):
        self.steps += 1
        if self.steps > MAX_STEPS or depth > MAX_DEPTH:
            raise InvalidCandidate("Execution budget exceeded")

    def run(self, args):
        self.steps = 0
        self.decision_return = None
        try:
            value = self.call("decision", list(args), 0)
            if type(value) is not bool:
                raise self.invalid("decision must return exactly bool", self.decision_return,
                                   message=f"Функция decision вернула {type(value).__name__}. "
                                           "Верните True или False для каждого входа.",
                                   kind="return_type")
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
            if name == "decision":
                self.decision_return = ret.node
            return checked(ret.value)
        return None

    def block(self, nodes, env, depth):
        for node in nodes:
            self.tick(depth)
            if isinstance(node, ast.Return):
                raise Returned(self.expr(node.value, env, depth + 1) if node.value else None, node)
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
