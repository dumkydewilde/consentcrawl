def batch(iterable, n=1):
    """
    Turn any iterable into a generator of batches of batch size n
    from: https://stackoverflow.com/a/8290508
    """
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx:min(ndx + n, l)]