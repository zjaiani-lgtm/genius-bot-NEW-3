import numpy as np

def walk_forward_optimize(data, window=200):
    best = None
    best_score = -1
    for param in np.linspace(1.0, 2.0, 10):
        score = np.random.rand()
        if score > best_score:
            best_score = score
            best = param
    print(f"Best param: {best}")
