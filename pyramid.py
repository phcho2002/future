def print_pyramid(layers=5):
    """打印指定层数的星号金字塔

    Args:
        layers: 金字塔层数，默认为5
    """
    for i in range(1, layers + 1):
        spaces = ' ' * (layers - i)
        stars = '*' * (2 * i - 1)
        print(spaces + stars)


if __name__ == '__main__':
    print_pyramid()
