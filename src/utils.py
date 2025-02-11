import re


def memory_unit_conversion(str_to_convert):
    """
    Convert a string representing a size in bytes to GiB.
    string structure: "2131Ki" or "2131Mi" or "2131Gi" or "2131Ti"

    @param str_to_convert: The string to convert
    @return The size in GiB
    """

    if not str_to_convert:
        return 0

    conversion_dict = {
        "b": 1 / (1024**3),
        "ki": 1 / (1024**2),
        "mi": 1 / 1024,
        "gi": 1,
        "ti": 1024,
        "pi": 1024**2,
        "kb": 1 / (1000**2),
        "mb": 1 / 1000,
        "gb": 1,
        "tb": 1000,
        "pb": 1000**2,
    }
    str_to_convert = str_to_convert.lower()

    match = re.match(r"(\d+)([kmgtp].)?", str_to_convert)
    if match:
        size = int(match.group(1))
        if match.group(2):
            unit = match.group(2)
        else:
            unit = "b"

        return size * conversion_dict[unit]
    else:
        # TODO add logging here to log the error
        return 0


def cpu_unit_conversion(str_to_convert):
    """
    Convert a string representing a number of CPUs to the number of cores.
    string structure: "2131m" or "2131"

    @param str_to_convert: The string to convert
    @return The number of cores
    """

    if not str_to_convert:
        return 0

    str_to_convert = str_to_convert.lower()
    conversion_dict = {
        "m": 1 / 1000,
        "c": 1,
    }

    match = re.match(r"(\d+)([mc])?", str_to_convert)
    if match:
        size = int(match.group(1))
        if match.group(2):
            unit = match.group(2)
        else:
            unit = "c"

        return size * conversion_dict[unit]
    else:
        # TODO add logging here to log the error
        return 0
