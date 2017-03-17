"""
:mod:`operalib.quantile` implements joint quantile regression with
Operator-Valued Kernels.
"""
# Authors: Maxime Sangnier <maxime.sangnier@gmail.com>
#          Romain Brault <romain.brault@telecom-paristech.fr> with help from
#          the scikit-learn community.
# License: MIT

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin

from cvxopt import matrix, solvers

from sklearn.utils import check_X_y, check_array
from sklearn.utils.validation import check_is_fitted
from sklearn.metrics.pairwise import rbf_kernel

from .kernels import DecomposableKernel

# When adding a new kernel, update this table and the _get_kernel_map method
PAIRWISE_KERNEL_FUNCTIONS = {
    'DGauss': DecomposableKernel,
    'DPeriodic': DecomposableKernel, }


class Quantile(BaseEstimator, RegressorMixin):
    """Joint quantile regression with operator-valued kernels.

    Joint quantile regression enables to learn and predict simultaneously
    several conditional quantiles (for prescribed quantile levels). Following
    the seminal work by Koenker and Basset (1978), the method minimizes an
    empirical risk based on the pinball loss and a squared-norm regularization.
    The hypothesis space considered here is the reproducing kernel Hilbert
    space generated by an operator-valued kernel chosen by the practitioner.

    Attributes
    ----------
    TODO !


    References
    ----------
    * R. Koenker and G. Bassett
      "Regression Quantiles", Econometrica, 46(1):33-50, 1978.
    * R. Koenker
      "Quantile Regression", Cambridge University Press, Cambridge, New York,
      2005.
    * M. Sangnier, O. Fercoq and F. d'Alche-Buc
      "Joint quantile regression in vector-valued RKHSs", hal-01272327, 2016.

    See also
    --------
    operalib.Ridge
        Operator-Valued kernel ridge regression
    sklearn.Ridge
        Linear ridge regression.
    sklearn.KernelRidge
        Kernel ridge regression.
    sklearn.SVR
        Support Vector Regression implemented using libsvm.

    Examples
    --------
    >>> import operalib as ovk
    >>> import numpy as np
    >>> n_samples, n_features = 10, 5
    >>> quantile_levels = [0.1, 0.5, 0.9]
    >>> rng = np.random.RandomState(0)
    >>> y = rng.randn(n_samples)
    >>> X = rng.randn(n_samples, n_features)
    >>> reg = ovk.Quantile('DGauss', lbda=1.0)
    >>> reg.fit(X, y)  # doctest: +NORMALIZE_WHITESPACE +ELLIPSIS
    Quantile(gamma=None, gamma_quantile=0.0, kernel='DGauss',
        kernel_params=None, lbda=1.0, nc_const=False, probs=0.5, tol=None,
        verbose=False)
    """

    def __init__(self, kernel='DGauss', probs=0.5, lbda=1e-5, gamma=None,
                 gamma_quantile=0., tol=None, nc_const=False,
                 kernel_params=None, verbose=False):
        """Initialize quantile regression model.

        Parameters
        ----------

        probs : {list}, default=[0.5]
            Probabilities (quantiles levels).

        kernel : {string, callable}, default='DGauss'
            Kernel mapping used internally. A callable should accept two
            arguments and the keyword arguments passed to this object as
            kernel_params, and should return a LinearOperator.

        lbda : {float}, default=1e-5
            Regularization parameter.  Lbda corresponds to ``C^-1`` in other
            large margin models such as SVC.

        gamma : {float}, default=None.
            Gamma parameter for the Decomposable Gaussian kernel.
            Ignored by other kernels.

        gamma_quantile : {float}, default=None.
            Gamma parameter for the output Gaussian kernel.

        tol : {float}, default=None
            Optimization tolerance (None leads to the default value of the
            solver).

        nc_const : {Boolean}, default=False
            Whether to enforce non-crossing constraints when optimizing
            (default is no).

        kernel_params : {mapping of string to any}, optional
            Additional parameters (keyword arguments) for kernel function
            passed as callable object.

        verbose : {Boolean}, default=False
            Verbosity
        """
        self.probs = probs
        self.lbda = lbda
        self.kernel = kernel
        self.gamma = gamma
        self.gamma_quantile = gamma_quantile
        self.tol = tol
        self.nc_const = nc_const
        self.kernel_params = kernel_params
        self.verbose = verbose

    def _validate_params(self):
        # check on self.kernel is performed in method __get_kernel
        if self.lbda < 0:
            raise ValueError('lbda must be positive')
        # if self.A < 0: # Check whether A is S PD would be really expensive
        #     raise ValueError('A must be a symmetric positive operator')
        if self.gamma is not None:
            if self.gamma < 0:
                raise ValueError('sigma must be positive or default (None)')
        if (self.probs_ < 0).any() or (self.probs_ > 1).any():
            raise ValueError('Probabilities must be in [0., 1.]')

    def _default_decomposable_op(self, y):
        self.probs_ = np.asarray([self.probs_]).T  # 2D array
        return rbf_kernel(self.probs_, gamma=self.gamma_quantile) \
            if self.gamma_quantile != np.inf else np.eye(self.probs_.size)

    def _get_kernel_map(self, X, y):
        # When adding a new kernel, update this table and the _get_kernel_map
        # method
        if callable(self.kernel):
            kernel_params = self.kernel_params or {}
            ov_kernel = self.kernel(**kernel_params)
        elif type(self.kernel) is str:
            # 1) check string and assign the right parameters
            if self.kernel == 'DGauss':
                self.A_ = self._default_decomposable_op(y)
                kernel_params = {'A': self.A_, 'scalar_kernel': rbf_kernel,
                                 'scalar_kernel_params': {'gamma': self.gamma}}
            else:
                raise NotImplementedError('unsupported kernel')
            # 2) Uses lookup table to select the right kernel from string
            ov_kernel = PAIRWISE_KERNEL_FUNCTIONS[self.kernel](**kernel_params)
        else:
            raise NotImplementedError('unsupported kernel')
        return ov_kernel(X)

    def _decision_function(self, X):
        n = X.shape[0]
        p = self.probs_.size

        pred = np.reshape(self.linop_(X) * self.coefs_, (n, p))
        pred += np.asarray(self.intercept_).ravel()

        return pred.T if self.linop_.p > 1 else pred.T.ravel()

    def predict(self, X):
        """Predict conditional quantiles.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape = [n_samples, n_features]
            Samples.

        Returns
        -------
        y : {array}, shape = [n_samples, n_quantiles]
            Returns predicted values for each prescribed quantile level.
        """
        check_is_fitted(self, ['coefs_', 'intercept_', 'linop_'],
                        all_or_any=all)
        X = check_array(X)
        return self._decision_function(X)

    def fit(self, X, y):
        """Fit joint quantile regression model.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape = [n_samples, n_features]
            Training data.

        y : {array-like}, shape = [n_samples]
            Target values.

        Returns
        -------
        self : returns an instance of self.
        """
        X, y = check_X_y(X, y, ['csr', 'csc', 'coo'], y_numeric=True)
        self.probs_ = np.array(self.probs, ndmin=1, copy=False)
        self._validate_params()

        self.linop_ = self._get_kernel_map(X, y)
        K = self.linop_.Gram_dense(X)

        self.C_ = 1. / self.lbda

        # Solve the optimization problem
        if self.nc_const:
            self._qp_nc(K, y)
        else:
            self._qp(K, y)
        return self

    def _qp_nc(self, K, y):
        # Needed to sort constraints on quantiles levels
        ind = np.argsort(self.probs_)

        p = np.size(self.probs_)  # Number of quantiles to predict
        n = K.shape[0]  # Number of coefficients
        m = int(n / p)  # Number of training instances
        l = m * (p - 1)  # Number of non-crossing dual variables
        probs = np.kron(np.ones(m), self.probs_.ravel())  # Quantiles levels

        D = -np.eye(p) + np.diag(np.ones(p - 1), 1)  # Difference matrix
        D = np.delete(D, -1, 0)
        D = D.T[np.argsort(ind)].T
        D = D.reshape((D.shape[0], D.shape[2]))

        # Quad. part of the objective function
        K = matrix(np.r_[np.c_[K, np.zeros((n, l))], np.zeros((l, n + l))])
        # Linear part of the objective
        q = matrix(np.r_[-np.kron(y, np.ones(p)), np.zeros(l)])
        # LHS of the inequality constraint
        G = matrix(np.r_[np.c_[np.eye(n), -np.kron(np.eye(m), D.T)],
                         np.c_[-np.eye(n), np.kron(np.eye(m), D.T)],
                         np.c_[np.zeros((l, n)), -np.eye(l)]])
        # RHS of the inequality constraint
        h = matrix(np.r_[self.C_ * probs, self.C_ * (1 - probs),
                   np.zeros((m * (p - 1)))])
        # LHS of the equality constraint
        A = matrix(np.c_[np.kron(np.ones(m), np.eye(p)), np.zeros((p, l))])
        # RHS of the equality constraint
        b = matrix(np.zeros(p))

        solvers.options['show_progress'] = self.verbose
        if self.tol:
            solvers.options['reltol'] = self.tol
        sol = solvers.qp(K, q, G, h, A, b)  # Solve the dual opt. problem

        # Set coefs
        # self.coefs = np.reshape(sol['x'][:n], (m, p)).T
        self.coefs_ = np.asarray(sol['x'][:n])

        # Set the intercept (the quantile property is not verified)
        self.intercept_ = np.asarray(sol['y']).squeeze()

    def _qp(self, K, y):
        p = np.size(self.probs_)  # Number of quantiles to predict
        n = K.shape[0]  # Number of variables
        probs = np.kron(np.ones(int(n / p)), self.probs_)  # Quantiles levels

        K = matrix(K)  # Quadratic part of the objective
        q = matrix(-np.kron(y, np.ones(p)))  # Linear part of the objective
        G = matrix(np.r_[np.eye(n), -np.eye(n)])  # LHS of the inequ. constr.
        # RHS of the inequ.
        h = matrix(np.r_[self.C_ * probs, 
                         self.C_ * (1 - probs)].reshape((-1, 1)))
        # LHS of the equ. constr.
        A = matrix(np.kron(np.ones(int(n / p)), np.eye(p)))
        b = matrix(np.zeros(p))  # RHS of the equality constraint

        solvers.options['show_progress'] = self.verbose
        if self.tol:
            solvers.options['reltol'] = self.tol
        self.sol_ = solvers.qp(K, q, G, h, A, b)  # Solve the dual opt. problem

        # Set coefs
        self.coefs_ = np.asarray(sol['x'])

        # Set the intercept
        self.intercept_ = 0.  # Erase the previous intercept before prediction
        self.intercept_ = [
            np.percentile(y - pred, 100. * prob) for
            (pred, prob) in zip(self.predict(self.linop_.X), self.probs_)]
        self.intercept_ = np.asarray(self.intercept_)

    def pinball_loss(self, pred, y):
        """Compute the pinball loss.

        Parameters
        ----------
        pred : {array-like}, shape = [n_quantiles, n_samples] or [n_samples]
            Predictions.

        y : {array-like}, shape = [n_samples]
            Targets.

        Returns
        -------
        l : {array}, shape = [n_quantiles]
            Average loss for each quantile level.
        """
        if pred.ndim == 1:
            pred = pred.reshape((1, -1))
        pred, y = check_X_y(pred.T, y)
        pred = pred.T
        residual = y - pred
        loss = np.sum([np.fmax(prob * res, (prob - 1) * res) for (res, prob) in
                      zip(residual, self.probs_)], axis=1)
        loss *= 1. / y.size
        return loss

    def score(self, X, y, sample_weight=None):
        """Compute the pinball score for the given dataset.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape = [n_samples, n_features]
            Training data.

        y : {array-like}, shape = [n_samples]
            Target values.

        Returns
        -------
        l : {float}
            Average pinball score (the higher, the better).
        """
        check_is_fitted(self, ['coefs_', 'intercept_', 'linop_'],
                        all_or_any=all)
        pred, y = check_X_y(X, y)
        return 1 - self.pinball_loss(self.predict(X), y).mean()
