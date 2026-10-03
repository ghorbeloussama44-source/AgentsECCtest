def total(items):
    t = 0
    for i in range(len(items)):
        if items[i]["qty"] > 0:
            if items[i]["price"] > 0:
                t = t + items[i]["qty"] * items[i]["price"]
    return t

def discount(t, code):
    if code == "SAVE10":
        return t - t * 0.1
    elif code == "SAVE20":
        return t - t * 0.2
    elif code == "SAVE30":
        return t - t * 0.3
    else:
        return t

def fmt(t):
    return "$" + str(round(t, 2))
