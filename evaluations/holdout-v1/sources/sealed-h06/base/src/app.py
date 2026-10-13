def partition(index, width):
    'Partition built-in integer coordinates with a positive built-in integer width.'
    if type(index) is not int or type(width) is not int or width <= 0:
        raise ValueError('integer coordinates and positive integer width required')
    return index // width, index % width
