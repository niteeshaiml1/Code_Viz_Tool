"""
parser.py
Core AST parser — extracts functions, classes, loops, conditions,
variables, calls, and builds a dependency map from Python source code.
"""
import ast
import textwrap
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FunctionInfo:
    name: str
    line_start: int
    line_end: int
    args: list[str]
    docstring: Optional[str]
    source: str
    calls: list[str] = field(default_factory=list)
    complexity: int = 1


@dataclass
class FlowNode:
    node_id: int
    node_type: str          
    label: str
    line: int
    depth: int
    children: list[int] = field(default_factory=list)


@dataclass
class ParseResult:
    functions: list[FunctionInfo]
    flow_nodes: list[FlowNode]
    dependency_map: dict        
    issues: list[dict]
    stats: dict


class CodeParser(ast.NodeVisitor):
    def __init__(self, source: str):
        self.source = source
        self.source_lines = source.splitlines()
        self.functions: list[FunctionInfo] = []
        self.flow_nodes: list[FlowNode] = []
        self.dependency_map: dict = {}
        self.issues: list[dict] = []
        self._node_counter = 0
        self._current_func: Optional[str] = None
        self._depth = 0

    def _next_id(self) -> int:
        self._node_counter += 1
        return self._node_counter

    def _get_source_lines(self, node) -> str:
        try:
            start = node.lineno - 1
            end = getattr(node, 'end_lineno', node.lineno)
            lines = self.source_lines[start:end]
            return textwrap.dedent('\n'.join(lines))
        except Exception:
            return ""

    def _add_node(self, node_type: str, label: str, line: int) -> FlowNode:
        n = FlowNode(
            node_id=self._next_id(),
            node_type=node_type,
            label=label,
            line=line,
            depth=self._depth
        )
        self.flow_nodes.append(n)
        return n

    def visit_FunctionDef(self, node):
        prev_func = self._current_func
        self._current_func = node.name
        self._depth += 1

        args = [a.arg for a in node.args.args]
        docstring = ast.get_docstring(node)
        src = self._get_source_lines(node)

        func_info = FunctionInfo(
            name=node.name,
            line_start=node.lineno,
            line_end=getattr(node, 'end_lineno', node.lineno),
            args=args,
            docstring=docstring,
            source=src,
        )
        self.functions.append(func_info)
        self.dependency_map[node.name] = []

        self._add_node("function", f"def {node.name}({', '.join(args)})", node.lineno)

        length = getattr(node, 'end_lineno', node.lineno) - node.lineno
        if length > 40:
            self.issues.append({
                "type": "long_function",
                "severity": "warning",
                "message": f"Function '{node.name}' is {length} lines long (recommended: < 40)",
                "line": node.lineno
            })

        self.generic_visit(node)
        self._current_func = prev_func
        self._depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._add_node("class", f"class {node.name}", node.lineno)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_For(self, node):
        try:
            target = ast.unparse(node.target)
            iter_ = ast.unparse(node.iter)
            label = f"for {target} in {iter_}"
        except Exception:
            label = "for loop"

        self._add_node("loop", label, node.lineno)
        self._depth += 1

        if self._depth > 3:
            self.issues.append({
                "type": "deep_nesting",
                "severity": "warning",
                "message": f"Deep nesting detected at line {node.lineno} (depth {self._depth})",
                "line": node.lineno
            })

        self.generic_visit(node)
        self._depth -= 1

    def visit_While(self, node):
        try:
            test = ast.unparse(node.test)
            label = f"while {test}"
        except Exception:
            label = "while loop"
        self._add_node("loop", label, node.lineno)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_If(self, node):
        try:
            test = ast.unparse(node.test)
            label = f"if {test}"
        except Exception:
            label = "if condition"
        self._add_node("condition", label, node.lineno)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_Return(self, node):
        try:
            val = ast.unparse(node.value) if node.value else "None"
            label = f"return {val}"
        except Exception:
            label = "return"
        self._add_node("return", label, node.lineno)

    def visit_Call(self, node):
        try:
            func_name = ast.unparse(node.func)
            self._add_node("call", f"{func_name}(...)", node.lineno)
            if self._current_func and func_name in self.dependency_map.get(self._current_func, []) is False:
                if self._current_func in self.dependency_map:
                    if func_name not in self.dependency_map[self._current_func]:
                        self.dependency_map[self._current_func].append(func_name)
        except Exception:
            pass
        self.generic_visit(node)

    def visit_Assign(self, node):
        try:
            targets = ', '.join(ast.unparse(t) for t in node.targets)
            value = ast.unparse(node.value)
            label = f"{targets} = {value}"
            if len(label) > 60:
                label = label[:57] + "..."
        except Exception:
            label = "assignment"
        self._add_node("assign", label, node.lineno)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.type is None:
            self.issues.append({
                "type": "bare_except",
                "severity": "error",
                "message": f"Bare 'except:' at line {node.lineno} — catches all exceptions including KeyboardInterrupt",
                "line": node.lineno
            })
        self.generic_visit(node)


def compute_complexity(tree) -> int:
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                              ast.With, ast.Assert, ast.comprehension)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
    return complexity


def parse_code(source: str) -> ParseResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f"Syntax error at line {e.lineno}: {e.msg}")

    parser = CodeParser(source)
    parser.visit(tree)

    for func in parser.functions:
        try:
            func_tree = ast.parse(func.source)
            func.complexity = compute_complexity(func_tree)
        except Exception:
            func.complexity = 1

    for func in parser.functions:
        try:
            func_tree = ast.parse(func.source)
            calls = []
            for node in ast.walk(func_tree):
                if isinstance(node, ast.Call):
                    try:
                        name = ast.unparse(node.func)
                        calls.append(name)
                    except Exception:
                        pass
            func.calls = list(set(calls))
            parser.dependency_map[func.name] = func.calls
        except Exception:
            pass

    lines = [l for l in source.splitlines() if l.strip()]
    stats = {
        "total_lines": len(source.splitlines()),
        "code_lines": len(lines),
        "functions": len(parser.functions),
        "classes": sum(1 for n in parser.flow_nodes if n.node_type == "class"),
        "loops": sum(1 for n in parser.flow_nodes if n.node_type == "loop"),
        "conditions": sum(1 for n in parser.flow_nodes if n.node_type == "condition"),
        "issues": len(parser.issues),
        "avg_complexity": round(
            sum(f.complexity for f in parser.functions) / max(len(parser.functions), 1), 1
        )
    }

    return ParseResult(
        functions=parser.functions,
        flow_nodes=parser.flow_nodes,
        dependency_map=parser.dependency_map,
        issues=parser.issues,
        stats=stats,
    )