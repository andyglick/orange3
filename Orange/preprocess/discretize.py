import numpy as np

from Orange.data import DiscreteVariable, Domain
from Orange.data.sql.table import SqlTable
from Orange.statistics import distribution, contingency
from .transformation import Transformation
from . import _discretize

__all__ = ["EqualFreq", "EqualWidth", "EntropyMDL", "DomainDiscretizer"]


class Discretizer(Transformation):
    """Value transformer that returns an index of the bin for the given value.
    """
    def __init__(self, variable, points):
        super().__init__(variable)
        self.points = points

    def transform(self, c):
        if c.size:
            return np.where(np.isnan(c), np.NaN, np.digitize(c, self.points))
        else:
            return np.array([], dtype=int)

    @staticmethod
    def _fmt_interval(low, high, decimals):
        assert low is None or high is None or low < high
        assert decimals >= 0

        def fmt_value(value, decimals):
            return (("%%.%if" % decimals) % value).rstrip("0").rstrip(".")

        if (low is None or np.isinf(low)) and \
                not (high is None or np.isinf(high)):
            return "<{}".format(fmt_value(high, decimals))
        elif (high is None or np.isinf(high)) and \
                not (low is None or np.isinf(low)):
            return ">={}".format(fmt_value(low, decimals))
        else:
            return "[{}, {})".format(fmt_value(low, decimals),
                                     fmt_value(high, decimals))

    @classmethod
    def create_discretized_var(cls, var, points):
        lpoints = list(points)
        if lpoints:
            values = [
                cls._fmt_interval(low, high, var.number_of_decimals)
                for low, high in zip([-np.inf] + lpoints, lpoints + [np.inf])]

            def discretized_attribute():
                return 'bin(%s, ARRAY%s)' % (var.to_sql(), str(lpoints))
        else:
            values = ["single_value"]

            def discretized_attribute():
                return "'%s'" % values[0]

        dvar = DiscreteVariable(name="D_" + var.name, values=values)
        dvar.compute_value = cls(var, points)
        dvar.source_variable = var
        dvar.to_sql = discretized_attribute
        return dvar


class Discretization:
    """Abstract base class for discretization classes."""
    def __call__(self, data, variable):
        """
        Compute discretization of the given variable on the given data.
        Return a new variable with the appropriate domain
        (:obj:`Orange.data.DiscreteVariable.values`) and transformer
        (:obj:`Orange.data.Variable.compute_value`).
        """
        raise NotImplementedError(
            "Subclasses of 'Discretization' need to implement "
            "the call operator")


class EqualFreq(Discretization):
    """Discretization into bins with approximately equal number of data
    instances.

    .. attribute:: n

        Number of bins (default: 4). The actual number may be lower if the
        variable has less than n distinct values.
    """
    def __init__(self, n=4):
        self.n = n

    # noinspection PyProtectedMember
    def __call__(self, data, attribute):
        if type(data) == SqlTable:
            att = attribute.to_sql()
            quantiles = [(i + 1) / self.n for i in range(self.n - 1)]
            query = data._sql_query(
                ['quantile(%s, ARRAY%s)' % (att, str(quantiles))])
            with data._execute_sql_query(query) as cur:
                points = sorted(set(cur.fetchone()[0]))
        else:
            d = distribution.get_distribution(data, attribute)
            points = _discretize.split_eq_freq(d, self.n)
        return Discretizer.create_discretized_var(
            data.domain[attribute], points)


class EqualWidth(Discretization):
    """Discretization into a fixed number of bins with equal widths.

    .. attribute:: n

        Number of bins (default: 4).
    """
    def __init__(self, n=4):
        self.n = n

    # noinspection PyProtectedMember
    def __call__(self, data, attribute, fixed=None):
        if fixed:
            min, max = fixed[attribute.name]
            points = self._split_eq_width_fixed(min, max, n=self.n)
        else:
            if type(data) == SqlTable:
                att = attribute.to_sql()
                query = data._sql_query(['min(%s)::double precision' % att,
                                         'max(%s)::double precision' % att])
                with data._execute_sql_query(query) as cur:
                    min, max = cur.fetchone()
                dif = (max - min) / self.n
                points = [min + (i + 1) * dif for i in range(self.n - 1)]
            else:
                # TODO: why is the whole distribution computed instead of
                # just min/max
                d = distribution.get_distribution(data, attribute)
                points = self._split_eq_width(d, n=self.n)
        return Discretizer.create_discretized_var(
            data.domain[attribute], points)

    @staticmethod
    def _split_eq_width(dist, n):
        min = dist[0][0]
        max = dist[0][-1]
        if min == max:
            return []
        dif = (max - min) / n
        return [min + (i + 1) * dif for i in range(n - 1)]

    @staticmethod
    def _split_eq_width_fixed(min, max, n):
        if min == max:
            return []
        dif = (max - min) / n
        return [min + (i + 1) * dif for i in range(n - 1)]


# noinspection PyPep8Naming
class EntropyMDL(Discretization):
    """
    Discretization into bins inferred by recursively splitting the values to
    minimize the class-entropy. The procedure stops when further splits would
    decrease the entropy for less than the corresponding increase of minimal
    description length (MDL). [FayyadIrani93].

    If there are no suitable cut-off points, the procedure returns a single bin,
    which means that the new feature is constant and can be removed.

    .. attribute:: force

        Induce at least one cut-off point, even when its information
        gain is lower than MDL (default: False).

    """
    def __init__(self, force=False):
        self.force = force

    def __call__(self, data, attribute):
        cont = contingency.get_contingency(data, attribute)
        values, I = cont.values, cont.counts.T
        cut_ind = np.array(self._entropy_discretize_sorted(I, self.force))
        if len(cut_ind) > 0:
            # "the midpoint between each successive pair of examples" (FI p.1)
            points = (values[cut_ind] + values[cut_ind - 1]) / 2.
        else:
            points = []
        return Discretizer.create_discretized_var(
            data.domain[attribute], points)

    @classmethod
    def _normalize(cls, X, axis=None, out=None):
        """
        Normalize `X` array so it sums to 1.0 over the `axis`.

        Parameters
        ----------
        X : array
            Array to normalize.
        axis : optional int
            Axis over which the resulting array sums to 1.
        out : optional array
            Output array of the same shape as X.
        """
        X = np.asarray(X, dtype=float)
        scale = np.sum(X, axis=axis, keepdims=True)
        if out is None:
            return X / scale
        else:
            if out is not X:
                assert out.shape == X.shape
                out[:] = X
            out /= scale
            return out

    @classmethod
    def _entropy_normalized(cls, D, axis=None):
        """
        Compute the entropy of distribution array `D`.

        `D` must be a distribution (i.e. sum to 1.0 over `axis`)

        Parameters
        ----------
        D : array
            Distribution.
        axis : optional int
            Axis of `D` along which to compute the entropy.

        """
        # req: (np.sum(D, axis=axis) >= 0).all()
        # req: (np.sum(D, axis=axis) <= 1).all()
        # req: np.all(np.abs(np.sum(D, axis=axis) - 1) < 1e-9)

        D = np.asarray(D)
        Dc = np.clip(D, np.finfo(D.dtype).eps, 1.0)
        return - np.sum(D * np.log2(Dc), axis=axis)

    @classmethod
    def _entropy(cls, D, axis=None):
        """
        Compute the entropy of distribution `D`.

        Parameters
        ----------
        D : array
            Distribution.
        axis : optional int
            Axis of `D` along which to compute the entropy.

        """
        D = cls._normalize(D, axis=axis)
        return cls._entropy_normalized(D, axis=axis)

    @classmethod
    def _entropy1(cls, D):
        """
        Compute the entropy of distributions in `D`
        (one per each row).
        """
        D = cls._normalize(D)
        return _discretize.entropy_normalized1(D)

    @classmethod
    def _entropy2(cls, D):
        """
        Compute the entropy of distributions in `D`
        (one per each row).
        """
        D = cls._normalize(D, axis=1)
        return _discretize.entropy_normalized2(D)

    @classmethod
    def _entropy_cuts_sorted(cls, CS):
        """
        Return the class information entropy induced by partitioning
        the `CS` distribution at all N-1 candidate cut points.

        Parameters
        ----------
        CS : (N, K) array of class distributions.
        """
        CS = np.asarray(CS)
        # |--|-------|--------|
        #  S1    ^       S2
        # S1 contains all points which are <= to cut point
        # Cumulative distributions for S1 and S2 (left right set)
        # i.e. a cut at index i separates the CS into S1Dist[i] and S2Dist[i]
        S1Dist = np.cumsum(CS, axis=0)[:-1]
        S2Dist = np.cumsum(CS[::-1], axis=0)[-2::-1]

        # Entropy of S1[i] and S2[i] sets
        ES1 = cls._entropy2(S1Dist)
        ES2 = cls._entropy2(S2Dist)

        # Number of cases in S1[i] and S2[i] sets
        S1_count = np.sum(S1Dist, axis=1)
        S2_count = np.sum(S2Dist, axis=1)

        # Number of all cases
        S_count = np.sum(CS)

        ES1w = ES1 * S1_count / S_count
        ES2w = ES2 * S2_count / S_count

        # E(A, T; S) Class information entropy of the partition S
        E = ES1w + ES2w

        return E, ES1, ES2

    @classmethod
    def _entropy_discretize_sorted(cls, C, force=False):
        """
        Entropy discretization on a sorted C.

        :param C: (N, K) array of class distributions.

        """
        E, ES1, ES2 = cls._entropy_cuts_sorted(C)
        # TODO: Also get the left right distribution counts from
        # entropy_cuts_sorted,

        # Note the + 1
        if len(E) == 0:
            return []
        cut_index = np.argmin(E) + 1

        # Distribution of classed in S1, S2 and S
        S1_c = np.sum(C[:cut_index], axis=0)
        S2_c = np.sum(C[cut_index:], axis=0)
        S_c = S1_c + S2_c

        ES = cls._entropy1(np.sum(C, axis=0))
        ES1, ES2 = ES1[cut_index - 1], ES2[cut_index - 1]

        # Information gain of the best split
        Gain = ES - E[cut_index - 1]
        # Number of different classes in S, S1 and S2
        k = float(np.sum(S_c > 0))
        k1 = float(np.sum(S1_c > 0))
        k2 = float(np.sum(S2_c > 0))

        assert k > 0
        delta = np.log2(3 ** k - 2) - (k * ES - k1 * ES1 - k2 * ES2)
        N = float(np.sum(S_c))

        if Gain > np.log2(N - 1) / N + delta / N:
            # Accept the cut point and recursively split the subsets.
            left, right = [], []
            if k1 > 1 and cut_index > 1:
                left = cls._entropy_discretize_sorted(C[:cut_index, :])
            if k2 > 1 and cut_index < len(C) - 1:
                right = cls._entropy_discretize_sorted(C[cut_index:, :])
            return left + [cut_index] + [i + cut_index for i in right]
        elif force:
            return [cut_index]
        else:
            return []


class DomainDiscretizer:
    """Discretizes all continuous features in the data.

    .. attribute:: method

        Feature discretization method (instance of
        :obj:`Orange.preprocess.Discretization`). If `None` (default),
        :class:`Orange.preprocess.EqualFreq` with 4 intervals is
        used.

    .. attribute:: clean

        If `True`, features discretized into a single interval constant are
        removed. This is useful for discretization methods that infer the
        number of intervals from the data, such as
        :class:`Orange.preprocess.EntropyMDL` (default: `True`).

    .. attribute:: discretize_class

        Determines whether a target is also discretized if it is continuous.
        (default: `False`)
    """
    def __new__(cls, data=None,
                discretize_class=False, method=None, clean=True, fixed=None):
        self = super().__new__(cls)
        self.discretize_class = discretize_class
        self.method = method
        self.clean = clean
        if data is None:
            return self
        else:
            return self(data, fixed)

    def __call__(self, data, fixed=None):
        """
        Compute and return discretized domain.

        :param data: Data to discretize.
        """

        def transform_list(s, fixed=None):
            new_vars = []
            for var in s:
                if var.is_continuous:
                    if fixed and var.name in fixed.keys():
                        nv = method(data, var, fixed)
                    else:
                        nv = method(data, var)
                    if not self.clean or len(nv.values) > 1:
                        new_vars.append(nv)
                else:
                    new_vars.append(var)
            return new_vars
        if self.method is None:
            method = EqualFreq(n=4)
        else:
            method = self.method
        domain = data.domain
        new_attrs = transform_list(domain.attributes, fixed)
        if self.discretize_class:
            new_classes = transform_list(domain.class_vars)
        else:
            new_classes = domain.class_vars
        return Domain(new_attrs, new_classes)
