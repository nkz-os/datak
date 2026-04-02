"""Sandboxed formula evaluation engine using RestrictedPython."""

import math
import re
from typing import Any

from RestrictedPython import compile_restricted_exec, safe_globals
from RestrictedPython.Eval import default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence, safer_getattr

# Allowed mathematical functions for formulas
ALLOWED_BUILTINS = {
    # Math functions
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sum": sum,
    "len": len,
    # Math module functions
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "degrees": math.degrees,
    "radians": math.radians,
    "floor": math.floor,
    "ceil": math.ceil,
    "fabs": math.fabs,
    # Constants
    "pi": math.pi,
    "e": math.e,
    # Type conversions (safe)
    "int": int,
    "float": float,
    "bool": bool,
}

# Pattern to detect dangerous imports and statements
FORBIDDEN_PATTERNS = [
    r"\bimport\b",
    r"\bexec\b",
    r"\beval\b",
    r"\bopen\b",
    r"\bfile\b",
    r"\b__\w+__\b",  # Dunder methods
    r"\bos\b",
    r"\bsys\b",
    r"\bsubprocess\b",
    r"\bsocket\b",
    r"\bglobals\b",
    r"\blocals\b",
    r"\bgetattr\b",
    r"\bsetattr\b",
    r"\bdelattr\b",
    r"\bcompile\b",
]


class FormulaError(Exception):
    """Exception raised for formula validation or execution errors."""

    pass


def validate_formula(formula: str) -> tuple[bool, str | None]:
    """
    Validate a formula string for safety.

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not formula or not formula.strip():
        return False, "Formula cannot be empty"

    # Check for forbidden patterns
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, formula, re.IGNORECASE):
            return False, f"Forbidden pattern detected: {pattern}"

    # Try to compile with RestrictedPython
    try:
        # Wrap formula in assignment to make it a valid statement
        # Use 'res' instead of '_result_' because RestrictedPython forbids starting with _
        code_str = f"res = {formula}"
        result = compile_restricted_exec(code_str)

        if result.errors:
            return False, "; ".join(result.errors)

        return True, None
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Compilation error: {e}"


def evaluate_formula(
    formula: str,
    val: float,
    extra_vars: dict[str, Any] | None = None,
) -> float:
    """
    Safely evaluate a formula with 'val' as the input variable.

    Args:
        formula: Mathematical expression string (e.g., "val * 0.1 + 10")
        val: The input value to transform
        extra_vars: Additional variables to make available

    Returns:
        The computed result as a float

    Raises:
        FormulaError: If formula is invalid or execution fails

    Examples:
        >>> evaluate_formula("val * 2", 10)
        20.0
        >>> evaluate_formula("sqrt(val) + 5", 16)
        9.0
        >>> evaluate_formula("val / 100", 2540)
        25.4
    """
    # Validate first
    is_valid, error = validate_formula(formula)
    if not is_valid:
        raise FormulaError(f"Invalid formula: {error}")

    # Build restricted globals
    restricted_globals = safe_globals.copy()
    restricted_globals["__builtins__"] = ALLOWED_BUILTINS
    restricted_globals["_getiter_"] = default_guarded_getiter
    restricted_globals["_getattr_"] = safer_getattr
    restricted_globals["_iter_unpack_sequence_"] = guarded_iter_unpack_sequence

    # Build local variables
    local_vars: dict[str, Any] = {
        "val": val,
        "value": val,  # Alias
        "x": val,  # Another alias
        "_result_": None,
    }

    if extra_vars:
        # Filter extra_vars to only include safe types
        for k, v in extra_vars.items():
            if isinstance(v, (int, float, bool, str)) and not k.startswith("_"):
                local_vars[k] = v

    # Compile and execute
    try:
        # Use 'res' instead of '_result_'
        code_str = f"res = {formula}"
        result = compile_restricted_exec(code_str)

        if result.errors:
            raise FormulaError(f"Compilation errors: {'; '.join(result.errors)}")

        exec(result.code, restricted_globals, local_vars)

        output = local_vars.get("res")

        if output is None:
            raise FormulaError("Formula produced no result")

        return float(output)

    except FormulaError:
        raise
    except (TypeError, ValueError) as e:
        raise FormulaError(f"Type error in formula: {e}") from e
    except ZeroDivisionError as e:
        raise FormulaError("Division by zero") from e
    except Exception as e:
        raise FormulaError(f"Formula execution failed: {e}") from e


def test_formula(formula: str, test_value: float = 100.0) -> dict[str, Any]:
    """
    Test a formula with a sample value.

    Returns dict with:
        - valid: bool
        - result: float | None
        - error: str | None
    """
    try:
        result = evaluate_formula(formula, test_value)
        return {"valid": True, "result": result, "error": None}
    except FormulaError as e:
        return {"valid": False, "result": None, "error": str(e)}
