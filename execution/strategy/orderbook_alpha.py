def orderbook_signal(bids, asks, threshold: float):
    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])

    if ask_vol == 0:
        return False, 0.0

    imbalance = bid_vol / ask_vol
    return imbalance > threshold, imbalance
