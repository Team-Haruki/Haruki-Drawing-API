use num::rational::Ratio;
use num::{One, Signed, Zero};
use std::fmt;
use std::ops::{Add, Div, Mul, Neg, Sub};

/// A rational number type for bar positions, matching Python's `fractions.Fraction`.
/// Uses i64 internally which is sufficient for all bar/beat calculations.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Fraction(Ratio<i64>);

impl Fraction {
    pub fn new(numer: i64, denom: i64) -> Self {
        Fraction(Ratio::new(numer, denom))
    }

    pub fn from_integer(n: i64) -> Self {
        Fraction(Ratio::from_integer(n))
    }

    pub fn zero() -> Self {
        Fraction(Ratio::zero())
    }

    pub fn one() -> Self {
        Fraction(Ratio::one())
    }

    pub fn numer(&self) -> &i64 {
        self.0.numer()
    }

    pub fn denom(&self) -> &i64 {
        self.0.denom()
    }

    pub fn to_f64(&self) -> f64 {
        *self.0.numer() as f64 / *self.0.denom() as f64
    }

    pub fn floor(&self) -> i64 {
        self.0.floor().to_integer()
    }

    pub fn ceil(&self) -> i64 {
        self.0.ceil().to_integer()
    }

    pub fn trunc(&self) -> i64 {
        self.0.trunc().to_integer()
    }

    /// Matches Python's `Fraction.limit_denominator(max_denominator)`.
    /// Uses the Stern-Brocot / continued fraction algorithm.
    pub fn limit_denominator(&self, max_denominator: i64) -> Fraction {
        if *self.0.denom() <= max_denominator {
            return *self;
        }

        let (mut p0, mut q0) = (0i64, 1i64);
        let (mut p1, mut q1) = (1i64, 0i64);

        let n = *self.0.numer();
        let d = *self.0.denom();

        let mut n_rem = n;
        let mut d_rem = d;

        loop {
            let a = n_rem / d_rem;
            let (q2,) = (q0 + a * q1,);
            if q2 > max_denominator {
                break;
            }
            let p2 = p0 + a * p1;
            p0 = p1;
            q0 = q1;
            p1 = p2;
            q1 = q2;

            let new_rem = n_rem - a * d_rem;
            n_rem = d_rem;
            d_rem = new_rem;

            if d_rem == 0 {
                break;
            }
        }

        let k = (max_denominator - q0) / q1;
        let bound1 = Fraction::new(p0 + k * p1, q0 + k * q1);
        let bound2 = Fraction::new(p1, q1);

        let self_f64 = self.to_f64();
        if (bound2.to_f64() - self_f64).abs() <= (bound1.to_f64() - self_f64).abs() {
            bound2
        } else {
            bound1
        }
    }

    /// Parse a fraction from a string like "120", "3/4", "1.5"
    pub fn parse(s: &str) -> Option<Fraction> {
        let s = s.trim();
        if let Some(pos) = s.find('/') {
            let numer: i64 = s[..pos].trim().parse().ok()?;
            let denom: i64 = s[pos + 1..].trim().parse().ok()?;
            Some(Fraction::new(numer, denom))
        } else if let Ok(n) = s.parse::<i64>() {
            Some(Fraction::from_integer(n))
        } else if let Ok(f) = s.parse::<f64>() {
            Some(Fraction::from_f64(f))
        } else {
            None
        }
    }

    pub fn from_f64(f: f64) -> Fraction {
        // Use a reasonable precision
        let r = Ratio::approximate_float(f).unwrap_or_else(|| Ratio::from_integer(f as i64));
        Fraction(r)
    }

    pub fn abs(&self) -> Fraction {
        Fraction(self.0.abs())
    }

    pub fn inner(&self) -> Ratio<i64> {
        self.0
    }
}

impl fmt::Display for Fraction {
    /// Custom display matching Python: "1." for integers, "1.+1/2" for mixed, "1/2" for proper
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let i = self.trunc();
        if Fraction::from_integer(i) == *self {
            write!(f, "{i}.")
        } else if i == 0 {
            write!(f, "{}/{}", self.0.numer(), self.0.denom())
        } else {
            let remainder = *self - Fraction::from_integer(i);
            write!(
                f,
                "{i}.+{}/{}",
                remainder.0.numer(),
                remainder.0.denom()
            )
        }
    }
}

impl fmt::Debug for Fraction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self}")
    }
}

impl From<i64> for Fraction {
    fn from(n: i64) -> Self {
        Fraction::from_integer(n)
    }
}

impl From<i32> for Fraction {
    fn from(n: i32) -> Self {
        Fraction::from_integer(n as i64)
    }
}

impl From<f64> for Fraction {
    fn from(f: f64) -> Self {
        Fraction::from_f64(f)
    }
}

impl Add for Fraction {
    type Output = Fraction;
    fn add(self, rhs: Fraction) -> Fraction {
        Fraction(self.0 + rhs.0)
    }
}

impl Sub for Fraction {
    type Output = Fraction;
    fn sub(self, rhs: Fraction) -> Fraction {
        Fraction(self.0 - rhs.0)
    }
}

impl Mul for Fraction {
    type Output = Fraction;
    fn mul(self, rhs: Fraction) -> Fraction {
        Fraction(self.0 * rhs.0)
    }
}

impl Div for Fraction {
    type Output = Fraction;
    fn div(self, rhs: Fraction) -> Fraction {
        Fraction(self.0 / rhs.0)
    }
}

impl Neg for Fraction {
    type Output = Fraction;
    fn neg(self) -> Fraction {
        Fraction(-self.0)
    }
}

impl Mul<i64> for Fraction {
    type Output = Fraction;
    fn mul(self, rhs: i64) -> Fraction {
        Fraction(self.0 * rhs)
    }
}

impl Div<i64> for Fraction {
    type Output = Fraction;
    fn div(self, rhs: i64) -> Fraction {
        Fraction(self.0 / rhs)
    }
}

impl Add<i64> for Fraction {
    type Output = Fraction;
    fn add(self, rhs: i64) -> Fraction {
        Fraction(self.0 + Ratio::from_integer(rhs))
    }
}

impl Sub<i64> for Fraction {
    type Output = Fraction;
    fn sub(self, rhs: i64) -> Fraction {
        Fraction(self.0 - Ratio::from_integer(rhs))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_display() {
        assert_eq!(format!("{}", Fraction::from_integer(1)), "1.");
        assert_eq!(format!("{}", Fraction::new(1, 2)), "1/2");
        assert_eq!(format!("{}", Fraction::new(3, 2)), "1.+1/2");
        assert_eq!(format!("{}", Fraction::from_integer(0)), "0.");
    }

    #[test]
    fn test_arithmetic() {
        let a = Fraction::new(1, 2);
        let b = Fraction::new(1, 3);
        assert_eq!(a + b, Fraction::new(5, 6));
        assert_eq!(a - b, Fraction::new(1, 6));
        assert_eq!(a * b, Fraction::new(1, 6));
    }

    #[test]
    fn test_limit_denominator() {
        let f = Fraction::new(355, 113);
        let limited = f.limit_denominator(10);
        assert_eq!(limited, Fraction::new(22, 7));
    }
}
