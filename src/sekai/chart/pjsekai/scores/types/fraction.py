import fractions
import operator


class Fraction(fractions.Fraction):
    def __str__(self) -> str:
        i = int(self)
        if i == 0:
            return fractions.Fraction.__str__(self)
        if i == self:
            return f'{i}.'

        return f'{i}.+{fractions.Fraction.__str__(self - i)}'

    def __repr__(self) -> str:
        return str(self)

    def _wrap_result(f):
        def g(*args, **kwargs):
            ans = f(*args, **kwargs)
            if isinstance(ans, fractions.Fraction):
                ans = Fraction(ans)
            return ans
        return g

    __add__ = _wrap_result(fractions.Fraction.__add__)

    for f in (
        'limit_denominator',
        '__add__', '__radd__',
        '__sub__', '__rsub__',
        '__mul__', '__rmul__',
        '__truediv__', '__rtruediv__',
        '__floordiv__', '__rfloordiv__',
        '__mod__', '__rmod__',
        '__pow__', '__rpow__',
        '__pos__',
        '__neg__',
        '__abs__',
        '__trunc__',
        '__floor__',
        '__ceil__',
        '__round__',
    ):
        exec(f'{f} = _wrap_result(fractions.Fraction.{f})')


if __name__ == '__main__':
    a = Fraction(2, 2)
    a = a + 1
    print(a, type(a))
