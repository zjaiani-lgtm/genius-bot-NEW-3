import numpy as np
from sklearn.linear_model import LogisticRegression

class MLSignalFilter:
    def __init__(self):
        self.model = LogisticRegression()
        self.trained = False

    def train_dummy(self):
        X = np.random.rand(200, 3)
        y = (X.sum(axis=1) > 1.5).astype(int)
        self.model.fit(X, y)
        self.trained = True

    def predict_prob(self, features):
        if not self.trained:
            self.train_dummy()
        return float(self.model.predict_proba([features])[0][1])
