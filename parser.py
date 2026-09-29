def parse_int(data, i):
    end = data.index(b'e', i + 1)
    val = int(data[i + 1:end].decode())
    return val, end + 1

def parse_str(data, i):
    colon = data.index(b':', i)
    length = int(data[i:colon])
    start = colon + 1
    end = start + length
    return data[start:end], end

def parse_list(data, i):
    i += 1  # skip 'l'
    items = []
    while i < len(data) and data[i:i+1] != b'e':
        val, i = parse_any(data, i)
        items.append(val)
    return items, i + 1  # skip 'e'

def parse_dict(data, i):
    i += 1  # skip 'd'
    result = {}
    while i < len(data) and data[i:i+1] != b'e':
        key, i = parse_str(data, i)
        val, i = parse_any(data, i)
        result[key] = val
    return result, i + 1  # skip 'e'

def parse_any(data, i):
    char = data[i:i+1]
    if char == b'i':
        return parse_int(data, i)
    elif char == b'l':
        return parse_list(data, i)
    elif char == b'd':
        return parse_dict(data, i)
    elif char.isdigit():
        return parse_str(data, i)
    else:
        raise ValueError(f"Invalid bencode format at position {i}: {char}")

def bdecode(data):
    if not isinstance(data, bytes) or not data:
        raise ValueError("Input must be a non-empty bytes object")
    result, _ = parse_any(data, 0)
    return result

def bencode(data):
    if isinstance(data, int):
        return f"i{data}e".encode()
    elif isinstance(data, bytes):
        return str(len(data)).encode() + b':' + data
    elif isinstance(data, str):
        encoded = data.encode('utf-8')
        return str(len(encoded)).encode() + b':' + encoded
    elif isinstance(data, list):
        return b'l' + b''.join(bencode(x) for x in data) + b'e'
    elif isinstance(data, dict):
        items = []
        for key in sorted(data.keys()):
            encoded_key = key.encode('utf-8') if isinstance(key, str) else key
            items.append(bencode(encoded_key))
            items.append(bencode(data[key]))
        return b'd' + b''.join(items) + b'e'
    else:
        raise ValueError(f"Unsupported type: {type(data)}")

    
