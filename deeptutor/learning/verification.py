"""Bounded arithmetic, substitution and SI-unit equality checks.

This is deliberately not a symbolic theorem prover. Unsupported expressions
return unknown. No Python evaluation, imports, attributes or code execution.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
import re


@dataclass(frozen=True)
class Quantity:
    value: Fraction
    dimensions: tuple[int, int, int] = (0, 0, 0)  # length, mass, time


UNITS = {
    "m": Quantity(Fraction(1), (1, 0, 0)),
    "cm": Quantity(Fraction(1, 100), (1, 0, 0)),
    "mm": Quantity(Fraction(1, 1000), (1, 0, 0)),
    "km": Quantity(Fraction(1000), (1, 0, 0)),
    "kg": Quantity(Fraction(1), (0, 1, 0)),
    "g": Quantity(Fraction(1, 1000), (0, 1, 0)),
    "s": Quantity(Fraction(1), (0, 0, 1)),
    "min": Quantity(Fraction(60), (0, 0, 1)),
    "h": Quantity(Fraction(3600), (0, 0, 1)),
    "N": Quantity(Fraction(1), (1, 1, -2)),
    "J": Quantity(Fraction(1), (2, 1, -2)),
    "Pa": Quantity(Fraction(1), (-1, 1, -2)),
}


class Unsupported(ValueError):
    pass


class ResourceLimit(ValueError):
    pass


def quantity(text, variables=None):
    if len(text) > 512:
        raise ResourceLimit("expression length")
    text = text.strip().replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    text = re.sub(r"(?<=\d)\s*((?:[A-DF-Za-df-z]|[Ee](?![+-]?\d))[A-Za-z]*)", r"*\1", text)
    if any(abs(int(exponent)) > 100 for exponent in re.findall(r"\d[eE]([+-]?\d+)", text)):
        raise ResourceLimit("scientific exponent")
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ResourceLimit("expression complexity")
    names = {**UNITS, **(variables or {})}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            result = Quantity(Fraction(ast.get_source_segment(text, node)))
        elif isinstance(node, ast.Name) and node.id in names:
            result = names[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            result = Quantity(
                value.value * (-1 if isinstance(node.op, ast.USub) else 1), value.dimensions
            )
        elif isinstance(node, ast.BinOp):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if a.dimensions != b.dimensions:
                    raise Unsupported("addition has incompatible units")
                result = Quantity(
                    a.value + b.value * (-1 if isinstance(node.op, ast.Sub) else 1), a.dimensions
                )
            elif isinstance(node.op, (ast.Mult, ast.Div)):
                divide = isinstance(node.op, ast.Div)
                result = Quantity(
                    a.value / b.value if divide else a.value * b.value,
                    tuple(x - y if divide else x + y for x, y in zip(a.dimensions, b.dimensions)),
                )
            elif isinstance(node.op, ast.Pow):
                if b.dimensions != (0, 0, 0) or b.value.denominator != 1:
                    raise Unsupported("only integer powers are supported")
                exponent = int(b.value)
                if abs(exponent) > 12:
                    raise ResourceLimit("exponent")
                result = Quantity(a.value**exponent, tuple(x * exponent for x in a.dimensions))
            else:
                raise Unsupported("operator")
        else:
            raise Unsupported("expression")
        if max(result.value.numerator.bit_length(), result.value.denominator.bit_length()) > 2048:
            raise ResourceLimit("number size")
        return result

    return visit(tree.body)


def verify_equality(left: str, right: str, variables: dict[str, str] | None = None):
    try:
        if variables and len(variables) > 12:
            raise ResourceLimit("variables")
        bindings = {name: quantity(str(value)) for name, value in (variables or {}).items()}
        a, b = quantity(left, bindings), quantity(right, bindings)
        return {
            "status": "passed" if a == b else "failed",
            "left": left,
            "right": right,
            "method": "exact rational arithmetic with SI dimensions",
            "variables": variables or {},
            "scope": "This equality at the supplied values only; not a proof of the whole solution.",
        }
    except ResourceLimit as exc:
        return {"status": "resource_limit", "reason": str(exc)}
    except (Unsupported, SyntaxError, ValueError):
        return {
            "status": "unknown",
            "reason": "Expression or units are outside the supported checker.",
        }
    except (ZeroDivisionError, OverflowError):
        return {"status": "execution_failed", "reason": "The expression could not be evaluated."}
