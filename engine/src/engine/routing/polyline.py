"""Google encoded polyline。Valhalla 用 precision 6；座標一律以 (lon, lat) 表示。"""


def decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    factor = 10**precision
    points: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lon / factor, lat / factor))
    return points


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    chunks = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def encode(points: list[tuple[float, float]], precision: int = 6) -> str:
    factor = 10**precision
    out = []
    prev_lat = prev_lon = 0
    for lon, lat in points:
        lat_i, lon_i = round(lat * factor), round(lon * factor)
        out.append(_encode_value(lat_i - prev_lat))
        out.append(_encode_value(lon_i - prev_lon))
        prev_lat, prev_lon = lat_i, lon_i
    return "".join(out)
