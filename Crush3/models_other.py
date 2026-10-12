#!/usr/bin/env python3
"""
models_other.py

Models tried for comparison with the main ones (models_main.py). Train them with
force_model.py --models other, or --models all for one table with the main models too.

  RandomForest    the main models without the history columns: what history adds
  XGBoost
  Ridge           linear regression (median-filled, scaled). A floor: if it warns as early as
  Ridge_history   the trees, the signal is simple. With and without the history columns.
  Formula         no learning from the feature table: crowd pressure = density x speed variance
                  (Helbing's measure, the risk proxy in Luerig's continuum model), here
                  density * std_speed^2 per link. One monotone curve fitted on the training runs
                  turns it into newtons so it can raise the same 1062 N alarm. Left out when the
                  table has no density / std_speed columns.
"""

from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from models_main import random_forest, xgboost


def crowd_pressure(X):
    return (X["density"] * X["std_speed"].fillna(0.0) ** 2).to_numpy().reshape(-1, 1)


def ridge():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge())


def new_models(columns=None):
    """name -> (untrained model, does it get the history columns)."""
    models = {"RandomForest": (random_forest(), False), "XGBoost": (xgboost(), False),
              "Ridge": (ridge(), False), "Ridge_history": (ridge(), True)}
    if columns is None or {"density", "std_speed"} <= set(columns):
        models["Formula"] = (make_pipeline(FunctionTransformer(crowd_pressure),
                                           IsotonicRegression(out_of_bounds="clip")), False)
    return models
