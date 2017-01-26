# Test methods with long descriptive names can omit docstrings
# pylint: disable=missing-docstring
from Orange.modelling import KNNLearner
from Orange.regression import RandomForestRegressionLearner
from Orange.widgets.regression.owadaboostregression import OWAdaBoost
from Orange.widgets.tests.base import (
    WidgetTest, WidgetLearnerTestMixin, ParameterMapping
)


class TestOWAdaBoostRegression(WidgetTest, WidgetLearnerTestMixin):
    def setUp(self):
        self.widget = self.create_widget(
            OWAdaBoost, stored_settings={"auto_apply": False})
        self.init()
        # Needed because the fitter type needs to be set to regression in order
        # to be tested properly
        self.data = self.housing[:10]
        losses = [loss.lower() for loss in self.widget.losses]
        self.parameters = [
            ParameterMapping('loss', self.widget.reg_algorithm_combo, losses),
            ParameterMapping('learning_rate', self.widget.learning_rate_spin),
            ParameterMapping('n_estimators', self.widget.n_estimators_spin)]

    def test_input_learner(self):
        """Check if base learner properly changes with learner on the input"""
        # Check that the default learner is suitable for AdaBoost
        self.assertIsNotNone(
            self.widget.base_estimator,
            "The default base estimator should not be none")
        self.assertTrue(
            self.widget.base_estimator.supports_weights,
            "The default base estimator should support weights")
        default_base_estimator_cls = self.widget.base_estimator

        # Try a valid learner
        self.send_signal("Learner", RandomForestRegressionLearner())
        self.assertIsInstance(
            self.widget.base_estimator, RandomForestRegressionLearner,
            "The base estimator was not updated when valid learner on input")

        # Reset to none
        self.send_signal("Learner", None)
        self.assertIsInstance(
            self.widget.base_estimator, type(default_base_estimator_cls),
            "The base estimator was not reset to default when None on input")

    def test_input_learner_that_does_not_support_sample_weights(self):
        self.send_signal("Learner", KNNLearner())
        self.assertNotIsInstance(self.widget.base_estimator, KNNLearner)
        self.assertIsNone(self.widget.base_estimator)
        self.assertTrue(self.widget.Error.no_weight_support.is_shown())

    def test_error_message_cleared_when_valid_learner_on_input(self):
        # Disconnecting an invalid learner should use the default one and hide
        # the error
        self.send_signal("Learner", KNNLearner())
        self.send_signal('Learner', None)
        self.assertFalse(
            self.widget.Error.no_weight_support.is_shown(),
            'Error message was not hidden on input disconnect')
        # Connecting a valid learner should also reset the error message
        self.send_signal("Learner", KNNLearner())
        self.send_signal('Learner', RandomForestRegressionLearner())
        self.assertFalse(
            self.widget.Error.no_weight_support.is_shown(),
            'Error message was not hidden when a valid learner appeared on '
            'input')

    def test_input_learner_disconnect(self):
        """Check base learner after disconnecting learner on the input"""
        self.send_signal("Learner", RandomForestRegressionLearner())
        self.assertIsInstance(
            self.widget.base_estimator, RandomForestRegressionLearner)
        self.send_signal("Learner", None)
        self.assertEqual(self.widget.base_estimator,
                         self.widget.DEFAULT_BASE_ESTIMATOR)
