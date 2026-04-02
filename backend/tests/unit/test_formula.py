"""Unit tests for the formula engine."""

import pytest

from app.core.formula import (
    FormulaError,
    evaluate_formula,
    verify_formula,
    validate_formula,
)


class TestValidateFormula:
    """Tests for formula validation."""

    def test_valid_simple_formula(self):
        is_valid, error = validate_formula("val * 2")
        assert is_valid is True
        assert error is None

    def test_valid_math_formula(self):
        is_valid, _error = validate_formula("sqrt(val) + 10")
        assert is_valid is True

    def test_valid_complex_formula(self):
        is_valid, _error = validate_formula("(val * 0.1 + 10) / 2")
        assert is_valid is True

    def test_empty_formula_invalid(self):
        is_valid, error = validate_formula("")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_import_blocked(self):
        is_valid, error = validate_formula("import os")
        assert is_valid is False
        assert "Forbidden" in error

    def test_os_blocked(self):
        is_valid, _error = validate_formula("os.system('ls')")
        assert is_valid is False

    def test_dunder_blocked(self):
        is_valid, _error = validate_formula("val.__class__")
        assert is_valid is False

    def test_exec_blocked(self):
        is_valid, _error = validate_formula("exec('print(1)')")
        assert is_valid is False

    def test_syntax_error(self):
        is_valid, error = validate_formula("val * * 2")
        assert is_valid is False
        assert "Syntax" in error or "error" in error.lower()


class TestEvaluateFormula:
    """Tests for formula evaluation."""

    def test_identity(self):
        result = evaluate_formula("val", 42.0)
        assert result == 42.0

    def test_multiplication(self):
        result = evaluate_formula("val * 2", 10.0)
        assert result == 20.0

    def test_division(self):
        result = evaluate_formula("val / 100", 2540.0)
        assert result == 25.4

    def test_linear_transform(self):
        # y = mx + b
        result = evaluate_formula("val * 0.1 + 10", 100.0)
        assert result == 20.0

    def test_sqrt(self):
        result = evaluate_formula("sqrt(val)", 16.0)
        assert result == 4.0

    def test_power(self):
        result = evaluate_formula("pow(val, 2)", 5.0)
        assert result == 25.0

    def test_trig_functions(self):
        result = evaluate_formula("sin(0)", 0)
        assert result == 0.0

    def test_round(self):
        result = evaluate_formula("round(val, 2)", 3.14159)
        assert result == 3.14

    def test_min_max(self):
        result = evaluate_formula("max(val, 100)", 50.0)
        assert result == 100.0

    def test_value_alias(self):
        result = evaluate_formula("value * 2", 10.0)
        assert result == 20.0

    def test_x_alias(self):
        result = evaluate_formula("x * 2", 10.0)
        assert result == 20.0

    def test_division_by_zero(self):
        with pytest.raises(FormulaError) as exc_info:
            evaluate_formula("val / 0", 10.0)
        assert "zero" in str(exc_info.value).lower()

    def test_invalid_formula_raises(self):
        with pytest.raises(FormulaError):
            evaluate_formula("import os", 10.0)


class TestTestFormula:
    """Tests for the formula testing helper."""

    def test_valid_formula(self):
        result = verify_formula("val * 2", 10.0)
        assert result["valid"] is True
        assert result["result"] == 20.0
        assert result["error"] is None

    def test_invalid_formula(self):
        result = verify_formula("import os", 10.0)
        assert result["valid"] is False
        assert result["result"] is None
        assert result["error"] is not None


class TestSecurityInjection:
    """Security tests to ensure malicious formulas are blocked."""

    @pytest.mark.parametrize(
        "malicious_formula",
        [
            "import os; os.system('reboot')",
            "__import__('os').system('ls')",
            "eval('print(1)')",
            "exec('x=1')",
            "open('/etc/passwd').read()",
            "globals()['__builtins__']",
            "locals()",
            "().__class__.__bases__[0].__subclasses__()",
            "getattr(val, '__class__')",
        ],
    )
    def test_malicious_formulas_blocked(self, malicious_formula: str):
        is_valid, _ = validate_formula(malicious_formula)
        assert is_valid is False, f"Formula should be blocked: {malicious_formula}"
